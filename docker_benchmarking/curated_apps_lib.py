import re
import sys
import subprocess
import time
from common.libs import utils
from common.config_files.constants import *


def curated_setup():
    print("Cleaning old contrib repo")
    rm_cmd = "rm -rf {}".format(ORIG_CURATED_PATH)
    utils.exec_shell_cmd(rm_cmd)
    print("Cloning and checking out Contrib Git Repo")
    utils.exec_shell_cmd(CONTRIB_GIT_CMD)
    if os.environ["curation_commit"]:
        update_gramine_branch(os.environ["curation_commit"])
        # This is to update the 'curation_commit' env var with
        # the commit id to support perf dashboard implementation.
        if os.environ["curation_commit"] == "master":
            git_commit_cmd = "git ls-remote https://github.com/gramineproject/gramine master"
            master_commit = utils.exec_shell_cmd(git_commit_cmd)
            os.environ["curation_commit"] = master_commit.split()[0][:7]


def copy_repo():
    copy_cmd = "cp -rf {} {}".format(ORIG_CURATED_PATH, REPO_PATH)
    utils.exec_shell_cmd("rm -rf contrib_repo")
    utils.exec_shell_cmd(copy_cmd)


def update_gramine_branch(commit):
    commit_str = f" && cd gramine && git checkout {commit} && cd .."
    copy_cmd = "cp config.yaml.template config.yaml"
    if not "v1" in commit:
        utils.exec_shell_cmd(f"cp -rf helper-files/{VERIFIER_TEMPLATE} {VERIFIER_DOCKERFILE}", None)
    utils.update_file_contents(copy_cmd, copy_cmd + "\nsed -i 's|Branch:.*master|Branch: \"{}|' config.yaml".format(commit), CURATION_SCRIPT)
    utils.update_file_contents(GRAMINE_1_4_CLONE, GRAMINE_1_4_CLONE.replace(DEPTH_STR, "") + commit_str,
            VERIFIER_DOCKERFILE)
    utils.update_file_contents(GRAMINE_1_4_CLONE, GRAMINE_1_4_CLONE.replace(DEPTH_STR, "") + commit_str,
        os.path.join(ORIG_BASE_PATH, "verifier", "helper.sh"))


def verify_image_creation(curation_output):
    if re.search("The curated GSC image gsc-(.*) is ready", curation_output) or \
            re.search("docker run", curation_output):
        return True
    return False


def generate_curated_image(test_config_dict):
    curation_output = ''
    workload_image = test_config_dict["docker_image"]
    logged_in_user = os.getlogin()
    cwd = os.getcwd()
    
    # The following ssh command is to mitigate the curses error faced while launching the command through Jenkins.
    curation_cmd = f"ssh -tt {logged_in_user}@localhost 'cd {CURATED_APPS_PATH} && python3 curate.py {workload_image} test'"

    print("Curation cmd ", curation_cmd)

    result = subprocess.run([curation_cmd], input=b'\x07', shell=True, check=True, stdout=subprocess.PIPE)
    os.chdir(cwd)
      
    curation_output = result.stdout.strip()
    print(curation_output.strip())

    return curation_output


def get_docker_run_command(workload_name):
    output = ''
    wrapper_image = "gsc-{}".format(workload_name)
    gsc_workload = "docker run --rm --net=host --device=/dev/sgx/enclave -t {}".format(wrapper_image)
    output += gsc_workload
    return output


def get_workload_result(test_config_dict):
    if "workload_result" in test_config_dict.keys():
        workload_result = [test_config_dict["workload_result"]]
    elif "bash" in test_config_dict["docker_image"]:
        workload_result = ["total        used        free      shared  buff/cache   available"]
    elif "redis" in test_config_dict["docker_image"]:
        workload_result = "Ready to accept connections"
    elif "pytorch" in test_config_dict["docker_image"]:
        workload_result = ["Done. The result was written to `result.txt`."]
    elif "sklearn" in test_config_dict["docker_image"]:
        workload_result = "Kmeans perf evaluation finished"
    elif "tensorflow-serving" in test_config_dict["docker_image"]:
        workload_result = "Running gRPC ModelServer at 0.0.0.0:8500"
    elif "mysql" in test_config_dict["docker_image"]:
        workload_result = "/usr/sbin/mysqld: ready for connections"
    return workload_result


def verify_process(test_config_dict, process=None, timeout=0):
    result = False
    docker_output = ''
    debug_log = None
    output = None
    workload_result = get_workload_result(test_config_dict)

    # Redirecting the debug mode logs to file instead of console because
    # it consumes whole lot of console and makes difficult to debug
    if test_config_dict.get("debug_mode") == "y":
        console_log_file = f"{LOGS_DIR}/{test_config_dict['test_name']}_console.log"
        debug_log = open(console_log_file, "w+")

    if timeout != 0:
        timeout = time.time() + timeout
    while True:
        if process.poll() is not None and output == '':
            break

        output = process.stdout.readline()
        
        if debug_log:
            if output: debug_log.write(output)
        else:
            if output: print(output.strip())

        if output:
            if output:
                docker_output += output
            if docker_output.count(workload_result) > 0:
                process.stdout.close()
                result = True
                break
            elif timeout != 0 and time.time() > timeout:
                break
    
    if debug_log: debug_log.close()
    return result


def run_curated_image(test_config_dict, docker_run_cmd):
    process = utils.popen_subprocess(docker_run_cmd)
    return verify_process(test_config_dict, process)

