import os
import time
import shutil
import pytest
from common.config_files.constants import *
from common.libs import utils


def fresh_gramine_checkout():
    """
    Function to perform a fresh checkout of gramine repo.
    :return:
    """
    print("\n###### In fresh_gramine_checkout #####\n")
    # Check if gramine folder exists. Delete it if exists, change directory to
    # user's home directory and git clone gramine within user's home dir.
    # Note: No need to create 'gramine' dir explicitly, as git clone will
    # automatically create one.
    # Also, note that we are checking out gramine everytime we execute the
    # performance framework, so that we execute it on the latest source.
    if os.path.exists(GRAMINE_HOME_DIR):
        shutil.rmtree(GRAMINE_HOME_DIR)
    
    print("\n-- Cloning Gramine git repo..\n", GRAMINE_CLONE_CMD)
    utils.exec_shell_cmd(GRAMINE_CLONE_CMD)
        
    # Git clone the examples repo too for workloads download.
    os.chdir(GRAMINE_HOME_DIR)

    commit_id = os.environ["commit_id"]
    if commit_id != '':
        utils.exec_shell_cmd(f"git checkout {commit_id}")
    else:
        commit_id = utils.exec_shell_cmd("git rev-parse HEAD")
        os.environ["commit_id"] = commit_id

    print("\n-- Checked out following Gramine commit: ", commit_id)

    print("\n-- Cloning Gramine examples git repo..\n", EXAMPLES_REPO_CLONE_CMD)
    utils.exec_shell_cmd(EXAMPLES_REPO_CLONE_CMD)
        
    os.chdir(FRAMEWORK_HOME_DIR)


def setup_gramine_environment():
    # Update the following environment variables as the gramine binaries can be
    # installed at some other place other than '/usr/local'
    # PATH, PYTHONPATH and PKG_CONFIG_PATH
    # Need to update these variables only after building gramine as there would be some
    # dereferences of few path values which are created only after successful build.

    utils.update_env_variables(BUILD_PREFIX)

    print("\n-- Generating gramine-sgx private key..\n", GRAMINE_SGX_GEN_PRIVATE_KEY_CMD)
    utils.exec_shell_cmd(GRAMINE_SGX_GEN_PRIVATE_KEY_CMD)


def gramine_package_install():
    print("Installing latest Gramine package\n")

    distro, distro_version = utils.get_distro_and_version()
    if distro == 'rhel':
        utils.exec_shell_cmd("sudo curl -fsSLo /etc/yum.repos.d/gramine.repo https://packages.gramineproject.io/rpm/gramine.repo")
        utils.exec_shell_cmd("sudo dnf -y install gramine")
        return

    if distro == 'ubuntu' and distro_version in ["18.04", "20.04", "22.04"]:
        if distro_version == '18.04':
            gramine_dist = sgx_dist = 'bionic'
        elif distro_version == '20.04':
            gramine_dist = sgx_dist = 'focal'
        else: # 22.04
            gramine_dist, sgx_dist = 'stable', 'focal'
    else:
        raise Exception("\n-- Failure: Unsupported distro for Gramine installation!!")

    utils.exec_shell_cmd("sudo curl -fsSLo /usr/share/keyrings/gramine-keyring.gpg https://packages.gramineproject.io/gramine-keyring.gpg")
    utils.exec_shell_cmd(f"echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/gramine-keyring.gpg] https://packages.gramineproject.io/ {gramine_dist} main' | sudo tee /etc/apt/sources.list.d/gramine.list")

    utils.exec_shell_cmd("curl -fsSL https://download.01.org/intel-sgx/sgx_repo/ubuntu/intel-sgx-deb.key | sudo apt-key add -")
    utils.exec_shell_cmd(f"echo 'deb [arch=amd64] https://download.01.org/intel-sgx/sgx_repo/ubuntu {sgx_dist} main' | sudo tee /etc/apt/sources.list.d/intel-sgx.list")

    utils.exec_shell_cmd(APT_UPDATE_CMD)
    utils.exec_shell_cmd("sudo apt-get -y install gramine")


def install_gramine_dependencies():
    print("\n###### In install_gramine_dependencies #####\n")

    # Determine the distro and the corresponding version.
    # Choose the respective packages.txt based on the distro version.
    distro, distro_version = utils.get_distro_and_version()
    print(f"\n-- Installing gramine dependencies for Distro: {distro}  Version: {distro_version}\n")
    
    if distro == 'ubuntu':
        # Read the system packages yaml file and update the actual system_packages string
        system_packages_path = os.path.join(FRAMEWORK_HOME_DIR, 'baremetal_benchmarking/config_files', SYSTEM_PACKAGES_FILE)
        system_packages = utils.read_config_yaml(system_packages_path)
        system_packages_str = system_packages['Default']

        # Read the python packages yaml file and update the actual python_packages string
        python_packages_path = os.path.join(FRAMEWORK_HOME_DIR, 'baremetal_benchmarking/config_files', PYTHON_PACKAGES_FILE)
        python_packages = utils.read_config_yaml(python_packages_path)
        python_packages_str = python_packages['Default']

        if system_packages.get(distro_version) is not None:
            system_packages_str = system_packages_str + ' ' + system_packages[distro_version]
        if python_packages.get(distro_version) is not None:
            python_packages_str = python_packages_str + ' ' + python_packages[distro_version]

    else:
        pytest.exit("\n***** Unknown / Unsupported Distro.. Exiting test session. *****")

    print("\n-- Executing below mentioned system update cmd..\n", APT_UPDATE_CMD)
    utils.exec_shell_cmd(APT_UPDATE_CMD)
    time.sleep(PKG_INSTALL_WAIT_TIME)

    print("\n-- Executing below mentioned apt --fix-broken cmd..\n", APT_FIX_BROKEN_CMD)
    utils.exec_shell_cmd(APT_FIX_BROKEN_CMD)
    time.sleep(PKG_INSTALL_WAIT_TIME)

    system_packages_cmd = SYS_PACKAGES_CMD + system_packages_str
    print("\n-- Executing below mentioned system packages installation cmd..\n", system_packages_cmd)
    utils.exec_shell_cmd(system_packages_cmd)
    time.sleep(PKG_INSTALL_WAIT_TIME)

    python_packages_cmd = PYTHON_PACKAGES_CMD + python_packages_str
    print("\n-- Executing below mentioned Python packages installation cmd..\n", python_packages_cmd)
    utils.exec_shell_cmd(python_packages_cmd)
    

def build_and_install_gramine():
    print("\n###### In build_and_install_gramine #####\n")
    
    # Change dir to above checked out gramine folder and
    # start building the same.
    os.chdir(GRAMINE_HOME_DIR)

    # Create prefix dir
    print(f"\n-- Creating build prefix directory '{BUILD_PREFIX}'..\n")
    # In the below makedirs call, if the target directory already exists an OSError is raised
    # if 'exist_ok' value is False. Otherwise, True value leaves the directory unaltered. 
    os.makedirs(BUILD_PREFIX, exist_ok=True)

    print("\n-- Executing below mentioned gramine-sgx sed cmd..\n", GRAMINE_SGX_SED_CMD)
    utils.exec_shell_cmd(GRAMINE_SGX_SED_CMD)
        
    print("\n-- Executing below mentioned gramine build meson build cmd..\n", GRAMINE_BUILD_MESON_CMD)
    utils.exec_shell_cmd(GRAMINE_BUILD_MESON_CMD)
    
    print("\n-- Executing below mentioned gramine ninja build cmd..\n", GRAMINE_NINJA_BUILD_CMD)
    utils.exec_shell_cmd(GRAMINE_NINJA_BUILD_CMD)

    print("\n-- Executing below mentioned gramine ninja build install cmd..\n", GRAMINE_NINJA_INSTALL_CMD)
    utils.exec_shell_cmd(GRAMINE_NINJA_INSTALL_CMD)
     
    os.chdir(FRAMEWORK_HOME_DIR)


def install_gramine_binaries():
    # Cleanup existing gramine binaries (if any) before starting a fresh build.
    # Passing prefix path as argument, so that user installed (if any) gramine
    # binaries are also removed.
    utils.cleanup_gramine_binaries(BUILD_PREFIX)

    fresh_gramine_checkout()
    
    if os.environ["build_gramine"] == "package":
        gramine_package_install()
    else:
        # Install Gramine dependencies
        install_gramine_dependencies()

        # Build and Install Gramine
        build_and_install_gramine()
    
    setup_gramine_environment()


def update_manifest_file(test_config_dict):
    src_file = os.path.join(FRAMEWORK_HOME_DIR, "baremetal_benchmarking/config_files" , test_config_dict['manifest_file'])
    dest_file = os.path.join(FRAMEWORK_HOME_DIR, test_config_dict['workload_home_dir'] , test_config_dict['manifest_name']) + ".manifest.template"

    shutil.copy2(src_file, dest_file)

    # Following 'if' condition is to mitigate the fix within the below commit,
    # which is not yet applied to package binaries: 4e724c10210a435c8837875fe2a4e8e50257b9c9
    # Since the fix is not applied on the package binaries, gramine-sgx execution will
    # fail when the framework is run with gramine package installation.
    # So, this if condition must be removed, once the above fix is applied on gramine package.
    if os.environ["build_gramine"] == "package":
        max_threads_cmd = f"sed -i 's/^sgx.max_threads/sgx.thread_num/' {dest_file}"
        print("\n-- Replacing sgx.max_threads with sgx.thread_num within the manifest file..")
        print(max_threads_cmd)
        utils.exec_shell_cmd(max_threads_cmd)

    print("EDMM enable is set to ", os.environ["EDMM"])
    if os.environ["EDMM"] == "1":
        disable_preheat_cmd = f"sed -i 's/sgx.preheat_enclave = true//' {dest_file}"
        print("\nDisabling preheat enclave when EDMM is enabled")
        print(disable_preheat_cmd)
        utils.exec_shell_cmd(disable_preheat_cmd)


def generate_sgx_token_and_sig(test_config_dict):
    sgx_exec = len(list(e_mode for e_mode in test_config_dict['exec_mode'] if 'gramine-sgx' in e_mode))
    if sgx_exec > 0:
        print("\n-- Generating SGX manifest..")
        sign_cmd = "gramine-sgx-sign --manifest {0}.manifest --output {0}.manifest.sgx".format(test_config_dict['manifest_name'])
        utils.exec_shell_cmd(sign_cmd)
