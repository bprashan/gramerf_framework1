import sys
import yaml
import time
import shutil
import psutil
import subprocess
import lsb_release
import csv
from datetime import datetime as dt
import collections
import pkg_resources
import pandas as pd
import socket
import netifaces as ni
import re
from common.config_files.constants import *


def verify_output(cmd_output, search_str): return re.search(search_str, cmd_output, re.IGNORECASE)


# calculate the percent degradation
def percent_degradation(tcd, baseline, testapp, throughput = False):
    if float(baseline) == 0:
        return 0
    if 'throughput' in tcd['test_name'] or throughput:
        return '{:0.3f}'.format(100 * (float(baseline) - float(testapp)) / float(baseline))
    else:
        return '{:0.3f}'.format(100 * (float(testapp) - float(baseline)) / float(baseline))


def exec_shell_cmd(cmd, stdout_val=subprocess.PIPE):
    try:
        cmd_stdout = subprocess.run([cmd], shell=True, check=True, stdout=stdout_val, stderr=subprocess.STDOUT, universal_newlines=True)

        if stdout_val is not None and cmd_stdout.stdout is not None:
            return cmd_stdout.stdout.strip()

        return cmd_stdout

    except subprocess.CalledProcessError as e:
        print(e.output)


def read_config_yaml(config_file_path):
    with open(config_file_path, "r") as config_fd:
        try:
            config_dict = yaml.safe_load(config_fd)
        except yaml.YAMLError as exc:
            raise Exception(exc)
    return config_dict


def get_distro_and_version():
    distro = lsb_release.get_distro_information().get('ID').lower()
    distro_version = lsb_release.get_distro_information().get('RELEASE')
    return distro, distro_version


def clear_system_cache():
    """
    Function to clear pagecache, dentries, and inodes. We need to clear system cache to get
    consistent results. This function can be removed after we implement the restart logic.
    :return:
    """
    echo_cmd_path = exec_shell_cmd('which echo')
    clear_cache_cmd = "sudo sh -c \"" + echo_cmd_path + " 3 > /proc/sys/vm/drop_caches\""
    print("\n-- Executing clear cache command..", clear_cache_cmd)
    exec_shell_cmd(clear_cache_cmd, None)


def clean_up_system():
    """
    Function to cleanup to remove unwanted packages and their dependencies, pagecache,
    dentries, inodes and apt cache.
    :return:
    """
    try:
        print("\n-- Removing unnecessary packages and dependencies..")
        exec_shell_cmd("sudo apt-get -y autoremove", None)
    except:
        print("\n-- Executing apt --fix-broken cmd..\n", APT_FIX_BROKEN_CMD)
        exec_shell_cmd(APT_FIX_BROKEN_CMD, None)
    print("\n-- Clearing thumbnail cache..")
    exec_shell_cmd("sudo rm -rf ~/.cache/thumbnails/*", None)
    print("\n-- Clearing apt cache..")
    exec_shell_cmd("sudo apt-get -y clean", None)
    if os.path.exists("/var/run/docker.sock"):
        print("\n-- Removing all docker images..")
        exec_shell_cmd("docker system prune -f", None)
        if os.environ["perf_config"] == "baremetal":
            print("\n-- Stopping docker service..")
            exec_shell_cmd("sudo systemctl stop docker", None)
        else:
            print("\n-- Restarting docker service..")
            exec_shell_cmd("sudo systemctl daemon-reload", None)
            exec_shell_cmd("sudo systemctl restart docker", None)

    clear_system_cache()

    print("\n-- Clearing swap memory..")
    exec_shell_cmd("sudo sh -c 'swapoff -a && swapon -a'", None)


def set_permissions():
    """
    Funciton to set appropriate permissions before tirggering the perf runs.
    :return:
    """
    print("\n-- Setting required device persmissions :")
    if os.path.exists("/dev/sgx_enclave") and os.path.exists("/dev/sgx_provision"):
        exec_shell_cmd("sudo chmod 777 /dev/sgx_enclave /dev/sgx_provision")
    else:
        print("\n-- Warning - Unable to find SGX dev files. May not be able to execute workload with SGX..")
    
    logged_in_user = os.getlogin()
    if os.path.exists("/dev/cpu_dma_latency"):
        exec_shell_cmd(f"sudo chown {logged_in_user} /dev/cpu_dma_latency")
        exec_shell_cmd("sudo chmod 0666 /dev/cpu_dma_latency")

    if os.path.exists("/var/run/docker.sock"):
        exec_shell_cmd(f"sudo chown {logged_in_user} /var/run/docker.sock", None)
        exec_shell_cmd("sudo chmod 666 /var/run/docker.sock")
    
    exec_shell_cmd("sudo mount -o remount,exec /dev")


def cleanup_gramine_binaries(build_prefix):
    """
    Function to clean up gramine binaries from standard system paths
    and user defined installed paths (installed via "--build_prefix" option)
    :param build_prefix:
    :return:
    """
    if os.path.exists(build_prefix): shutil.rmtree(build_prefix)

    gramine_uninstall_cmd = "sudo apt-get remove -y gramine"
    python_version_str = "python" + str(sys.version_info.major) + "." + str(sys.version_info.minor)
    # The substring "x86_64-linux-gnu" within below path is for Ubuntu. It would be different
    # for other distros like CentOS or RHEL. Currently, hardcoding it for Ubuntu but needs to
    # be updated for other distros in future.
    gramine_user_installed_bin_rm_cmd = "sudo rm -rf /usr/local/bin/gramine* /usr/local/lib/" + \
                                        python_version_str + \
                                        "/dist-packages/graminelibos /usr/local/lib/x86_64-linux-gnu/*gramine*"

    print("\n-- Uninstalling gramine..\n", gramine_uninstall_cmd)
    os.system(gramine_uninstall_cmd)

    print("\n-- Removing user installed gramine binaries..\n", gramine_user_installed_bin_rm_cmd)
    os.system(gramine_user_installed_bin_rm_cmd)


def update_env_variables(build_prefix):
    """
    Function to update the following environment variables to below respective locations,
    as the gramine binaries can be installed at some other place other than '/usr/local'.
    $PATH => <build_prefix>/bin
    $PYTHONPATH => <prefix>/lib/python<version>/site-packages
    $PKG_CONFIG_PATH => <prefix>/<libdir>/pkgconfig
    :param build_prefix:
    :return:
    """

    if os.environ["build_gramine"] != "package":
        # Update environment 'PATH' variable to the path (<prefix>/bin) where gramine
        # binaries would be installed.
        os.environ["PATH"] = build_prefix + "/bin" + os.pathsep + os.environ["PATH"]
        print(f"\n-- Updated environment PATH variable to the following..\n", os.environ["PATH"])

        # Update environment 'PKG_CONFIG_PATH' variable to <prefix>/<libdir>/pkgconfig.
        libdir_path_cmd = "meson introspect " + GRAMINE_HOME_DIR + \
                        "/build/ --buildoptions | jq -r '(map(select(.name == \"libdir\"))) | map(.value) | join(\"/\")'"
        libdir_path = exec_shell_cmd(libdir_path_cmd)

        os.environ["PKG_CONFIG_PATH"] = build_prefix + "/" + libdir_path + "/pkgconfig" + os.pathsep + os.environ.get(
            'PKG_CONFIG_PATH', '')
        print(f"\n-- Updated environment PKG_CONFIG_PATH variable to the following..\n", os.environ["PKG_CONFIG_PATH"])

        print(f"\n-- PYTHONPATH command\n", PYTHONPATH_CMD)
        os.environ["PYTHONPATH"] = exec_shell_cmd(PYTHONPATH_CMD)
        print(f"\n-- Updated environment PYTHONPATH variable to the following..\n", os.environ["PYTHONPATH"])

    print(f"\n-- Updating 'LC_ALL' env-var\n")
    os.environ['LC_ALL'] = "C.UTF-8"

    print(f"\n-- Updating 'LANG' env-var\n")
    os.environ['LANG'] = "C.UTF-8"

    print(f"\n-- Updating 'SSHPASS' env-var\n")
    os.environ['SSHPASS'] = "intel@123"

    print(f"\n-- Updating 'ARCH_LIBDIR' env-var\n")
    cmd_out = exec_shell_cmd('cc -dumpmachine')
    os.environ['ARCH_LIBDIR'] = "/lib/" + cmd_out

    print(f"\n-- Updating 'LC_ALL' env-var\n")
    os.environ['LC_ALL'] = "C.UTF-8"

    print(f"\n-- Updating 'LANG' env-var\n")
    os.environ['LANG'] = "C.UTF-8"
	
    os.environ['ENV_USER_UID'] = exec_shell_cmd('id -u')
    os.environ['ENV_USER_GID'] = exec_shell_cmd('id -g')


def set_http_proxies():
    """
    Function to set environment http and https proxies.
    :return:
    """
    os.environ['http_proxy'] = HTTP_PROXY
    os.environ['HTTP_PROXY'] = HTTP_PROXY
    os.environ['https_proxy'] = HTTPS_PROXY
    os.environ['HTTPS_PROXY'] = HTTPS_PROXY
    print("\n-- Setting http_proxy : \n", os.environ['http_proxy'])
    print("\n-- Setting https_proxy : \n", os.environ['https_proxy'])


def set_no_proxy():
    os.environ['no_proxy'] = NO_PROXY
    os.environ['NO_PROXY'] = NO_PROXY
    print("\n-- Setting no_proxy : \n", os.environ['no_proxy'])


def set_cpu_freq_scaling_governor():
    """
    Function to set the CPU frequency scaling governor to 'performance' mode.
    :return:
    """
    print("\n-- Setting CPU frequency scaling governor to 'performance' mode..")
    cpu_freq_file = os.path.join(FRAMEWORK_HOME_DIR, 'common/config_files', 'set_cpu_freq_scaling_governor.sh')

    chmod_cmd = 'chmod +x ' + cpu_freq_file
    set_cpu_freq_cmd = 'sudo ' + cpu_freq_file

    exec_shell_cmd(chmod_cmd)

    exec_shell_cmd(set_cpu_freq_cmd)


def set_threads_cnt_env_var():
    """
    Function to determine and set 'THREADS_CNT' env var.
    :return:
    """
    lscpu_output = exec_shell_cmd('lscpu')
    lines = lscpu_output.splitlines()
    cores_count, core_per_socket, threads_per_core = 0, 0, 0
    for line in lines:
        if 'CPU(s):' in line:
            cores_count = int(line.split(':')[-1].strip())
        if 'Core(s) per socket:' in line:
            core_per_socket = int(line.split(':')[-1].strip())
        if 'Thread(s) per core:' in line:
            threads_per_core = int(line.split(':')[-1].strip())
        if cores_count and core_per_socket and threads_per_core:
            break
    
    os.environ['CORES_COUNT'] = str(cores_count)
    os.environ['THREADS_CNT'] = str(core_per_socket * threads_per_core)
    os.environ['CORES_PER_SOCKET'] = str(core_per_socket)

    print("\n-- Setting the CORES_COUNT env variable to ", os.environ['CORES_COUNT'])
    print("\n-- Setting the THREADS_CNT env variable to ", os.environ['THREADS_CNT'])
    print("\n-- Setting the CORES_PER_SOCKET env variable to ", os.environ['CORES_PER_SOCKET'])


def determine_host_ip_addr():
    host_IP = socket.gethostbyname(socket.gethostname())
    
    if host_IP.startswith("127."):
        sock_obj = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # The IP address specified in below connect call doesn't have to be reachable..
        sock_obj.connect(('10.255.255.255', 1))
        host_IP = sock_obj.getsockname()[0]
        
    for ifaceName in ni.interfaces():
        if ni.ifaddresses(ifaceName).setdefault(ni.AF_INET) is not None and \
                ni.ifaddresses(ifaceName).setdefault(ni.AF_INET)[0]['addr'].startswith('192.168.0'):
            host_IP = ni.ifaddresses(ifaceName).setdefault(ni.AF_INET)[0]['addr']
            break

    return host_IP


def write_to_csv(tcd, test_dict):
    csv_res_folder = os.path.join(PERF_RESULTS_DIR, tcd['workload_name'])
    if not os.path.exists(csv_res_folder): os.makedirs(csv_res_folder)
    csv_res_file = os.path.join(csv_res_folder, tcd['test_name']+'.csv')
    with open(csv_res_file, 'w') as csvfile:
        csvwriter = csv.DictWriter(csvfile, test_dict.keys())
        csvwriter.writeheader()
        csvwriter.writerow(test_dict)
    

def write_to_report(workload_name, test_results):
    throughput_dict = collections.defaultdict(dict)
    latency_dict = collections.defaultdict(dict)
    generic_dict = collections.defaultdict(dict)

    for k in test_results:
        if 'throughput' in k:
            throughput_dict[k] = test_results[k]
        elif 'latency' in k:
            latency_dict[k] = test_results[k]
        else:
            generic_dict[k] = test_results[k]

    now = dt.isoformat(dt.now()).replace(":","-").split('.')[0]
    if workload_name == 'Tensorflow' and os.environ['encryption'] == '1':
        workload_name = 'tensorflow_encrypted'
    if os.environ['perf_config'] == "baremetal":
        commit_id = os.environ["commit_id"]
    else:
        commit_id = os.environ["curation_commit"]
    report_name = os.path.join(PERF_RESULTS_DIR, "gramine_" + workload_name.lower() + "_perf_data_" + now + "_" + commit_id[:7] + ".xlsx")
    if not os.path.exists(PERF_RESULTS_DIR): os.makedirs(PERF_RESULTS_DIR)
    if os.path.exists(report_name):
        writer = pd.ExcelWriter(report_name, engine='openpyxl', mode='a')
    else:
        writer = pd.ExcelWriter(report_name, engine='openpyxl')
    
    if workload_name == 'Redis' or workload_name == 'Memcached':
        cols = ['native', 'gramine-sgx-single-thread-non-exitless', 'gramine-sgx-diff-core-exitless', 'gramine-direct', \
                'native-avg', 'sgx-single-thread-avg', 'sgx-diff-core-exitless-avg', 'direct-avg', \
                'sgx-single-thread-deg', 'sgx-diff-core-exitless-deg', 'direct-deg']
    elif workload_name == 'Sklearnex':
        cols = ['data_type', 'dataset_name', 'rows', 'columns', 'classes', 'time', 'gramine-sgx', 'gramine-direct', 'gramine-sgx-deg', 'gramine-direct-deg']
    elif workload_name == 'TensorflowServing' or workload_name == 'MySql':
        cols = ['native', 'gramine-sgx', 'native-avg', 'sgx-avg', 'sgx-deg']
    else:
        cols = ['native', 'gramine-sgx', 'gramine-direct', 'native-avg', 'sgx-avg', 'direct-avg', 'sgx-deg', 'direct-deg']

    if len(throughput_dict) > 0:
        throughput_df = pd.DataFrame.from_dict(throughput_dict, orient='index', columns=cols).dropna(axis=1)
        throughput_df.columns = throughput_df.columns.str.upper()
        throughput_df.to_excel(writer, sheet_name=workload_name)

    if len(latency_dict) > 0:
        latency_df = pd.DataFrame.from_dict(latency_dict, orient='index', columns=cols).dropna(axis=1)
        latency_df.columns = latency_df.columns.str.upper()
        if len(throughput_dict) > 0:
            latency_df.to_excel(writer, sheet_name=workload_name, startcol=throughput_df.shape[1]+2)
        else:
            latency_df.to_excel(writer, sheet_name=workload_name)
    
    if len(generic_dict) > 0:
        if workload_name == 'Sklearnex':
            generic_df = pd.DataFrame.from_dict({(i,j): generic_dict[i][j] for i in generic_dict.keys() for j in generic_dict[i].keys()},
                                                orient='index', columns=cols)
            generic_df.rename(columns={'time':'native'}, inplace=True)
        else:
            generic_df = pd.DataFrame.from_dict(generic_dict, orient='index', columns=cols).dropna(axis=1)
        generic_df.columns = generic_df.columns.str.upper()
        generic_df.to_excel(writer, sheet_name=workload_name)

    writer.save()


def generate_performance_report(test_res_dict):
    print("\n###### In generate_performance_report #####\n")

    for workload, tests in test_res_dict.items():
        write_to_report(workload, tests)


def check_machine():
    service_cmd = "sudo systemctl --type=service --state=running"
    service_output = exec_shell_cmd(service_cmd)
    if "walinuxagent.service" in service_output:
        print("Running on Azure Linux Agent")
        return "Azure Linux Agent"
    elif "pccs.service" in service_output:
        print("Running on DCAP client")
        return "DCAP client"
    else:
        print("No Provisioning service found, cannot run tests with attestation.")
        return "No Provisioning enabled"


def kill(proc_pid):
    try:
        process = psutil.Process(proc_pid)
        for proc in process.children(recursive=True):
            proc.terminate()
        process.terminate()
    except:
        pass


def kill_process_by_name(processName):
    procs = [p.pid for p in psutil.process_iter() for c in p.cmdline() if processName in c]
    for process in procs:
        try:
            exec_shell_cmd("sudo kill -9 {}".format(process))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass


def get_workload_name(docker_image):
    try:
        return docker_image.split(" ")[1]
    except Exception as e:
        return ''


def cleanup_after_test(workload):
    try:
        kill_process_by_name("secret_prov_server_dcap")
        kill_process_by_name("/gramine/app_files/apploader.sh")
        kill_process_by_name("/gramine/app_files/entrypoint")
        exec_shell_cmd('sudo sh -c "echo 3 > /proc/sys/vm/drop_caches"')
        exec_shell_cmd("docker rmi gsc-{} -f".format(workload))
        exec_shell_cmd("docker rmi gsc-{}-unsigned -f".format(workload))
        exec_shell_cmd("docker rmi {} -f".format(workload))
        exec_shell_cmd("docker rmi verifier_image:latest -f")
        exec_shell_cmd("docker system prune -f")
    except Exception as e:
        pass


def popen_subprocess(command, dest_dir=None):
    if dest_dir:
        cwd = os.getcwd()
        os.chdir(dest_dir)

    print("Starting Process ", command)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, encoding='utf-8')
    time.sleep(1)
   
    if dest_dir: os.chdir(cwd)
    return process


def gen_encryption_key():
    enc_key_name = "encryption_key"
    exec_shell_cmd("gramine-sgx-pf-crypt gen-key -w " + enc_key_name)
    hex_enc_key_dump = exec_shell_cmd("xxd -p " + enc_key_name)
    return hex_enc_key_dump, enc_key_name


def is_package_installed(package_name):
    installed_packages = pkg_resources.working_set
    installed_packages_list = sorted(["%s==%s" % (i.key, i.version) for i in installed_packages])
    if any(package_name in j for j in installed_packages_list):
        return True
    else:
        return False

def read_file(filename):
    fd = open(filename)
    fd_contents = fd.read()
    fd.close()
    return fd_contents

def update_file_contents(old_contents, new_contents, filename, append=False):
    fd_contents = read_file(filename)
    if append:
        old_data = (old_contents).join(re.search("(.*){}(.*)".format(old_contents), fd_contents).groups())
        new_data = re.sub(old_data, new_contents+old_data, fd_contents)
    else:
        new_data = re.sub(old_contents, new_contents, fd_contents)
    fd = open(filename, "w")
    fd.write(new_data)
    fd.close()
