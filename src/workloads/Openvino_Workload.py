#
# Imports
#
from genericpath import exists
import pytest
import collections
import os
import subprocess
import time
import shutil
from src.libs.Workload import Workload
from src.config_files import constants
from src.libs import utils


# convert environment variable to boolean
def is_true(env):
    value = os.environ.get(env, 'false').lower()
    return value == 'true'

class OpenvinoWorkload(Workload):
    def install_workload(self, test_config_dict):
        # Installing Openvino within "/home/intel/test/gramine/examples/openvino/openvino_2021"
        install_dir = os.path.join(test_config_dict['workload_home_dir'], 'openvino_2021')
        
        # We would not install if the installation dir already exists.
        if os.path.exists(install_dir):
            print("\n-- Openvino already installed. Not fetching from source..")
            return
        os.makedirs(install_dir, exist_ok=True)
        cwd = os.getcwd()
        os.chdir(test_config_dict['workload_home_dir'])

        distro, distro_version = utils.get_distro_and_version()
        if distro == 'ubuntu':
            if distro_version == '18.04':
                wget_cmd = "wget https://storage.openvinotoolkit.org/repositories/openvino/packages/2021.4.2/l_openvino_toolkit_dev_ubuntu18_p_2021.4.752.tgz"
                untar_cmd = "tar xzf l_openvino_toolkit_dev_ubuntu18_p_2021.4.752.tgz"
                ren_dir = "l_openvino_toolkit_dev_ubuntu18_p_2021.4.752"
            elif distro_version == '20.04':
                wget_cmd = "wget https://storage.openvinotoolkit.org/repositories/openvino/packages/2021.4.2/l_openvino_toolkit_dev_ubuntu20_p_2021.4.752.tgz"
                untar_cmd = "tar xzf l_openvino_toolkit_dev_ubuntu20_p_2021.4.752.tgz"
                ren_dir = "l_openvino_toolkit_dev_ubuntu20_p_2021.4.752"
            else:
                print("\n-- Failure: Unsupported distro version for Openvino installation. Proceeding without installation..")
                os.chdir(cwd)
                return
        elif distro == 'rhel':
            wget_cmd = "wget https://storage.openvinotoolkit.org/repositories/openvino/packages/2021.4.2/l_openvino_toolkit_dev_rhel8_p_2021.4.752.tgz"
            untar_cmd = "tar xzf l_openvino_toolkit_dev_rhel8_p_2021.4.752.tgz"
            ren_dir = "l_openvino_toolkit_dev_rhel8_p_2021.4.752"
        else:
            print("\n-- Failure: Unsupported distro for Openvino installation. Proceeding without installation..")
            os.chdir(cwd)
            return

        print("\n-- Fetching Openvino workload from source..")
        subprocess.run(wget_cmd, shell=True, check=True)
        time.sleep(constants.SUBPROCESS_SLEEP_TIME)

        print("\n-- Extracting Openvino workload..")
        subprocess.run(untar_cmd, shell=True, check=True)
        time.sleep(constants.SUBPROCESS_SLEEP_TIME)
        
        os.rename(ren_dir, 'openvino_2021')

        os.chdir(cwd)
        

    '''
    This method renames the existing manifest to original manifest (as '.ori'), copies
    the manifest (latency/throughput) that needs to be overridden into the workload home dir
    and renames the copied manifest to the expected name by the workload.
    '''
    def replace_manifest_file(self, test_config_dict, buildForThroughput = None):
        original_manifest_file = os.path.join(test_config_dict['workload_home_dir'], test_config_dict['original_manifest_file'])
        
        # Check if the original manifest file exists.
        if not os.path.exists(original_manifest_file):
            print(f"\n-- Failure: Workload manifest {original_manifest_file} does not exist..")
            return False

        # Renaming the original manifest to manifest.ori
        os.rename(original_manifest_file, original_manifest_file+'.ori')

        if buildForThroughput == True:
            override_manifest_file = os.path.join(constants.FRAMEWORK_HOME_DIR, test_config_dict['throughput_manifest_file'])
        elif buildForThroughput == False:
            override_manifest_file = os.path.join(constants.FRAMEWORK_HOME_DIR, test_config_dict['latency_manifest_file'])
        
        # Copy the workload specific manifest to workload dir and 
        # rename the same as per the expected original name
        shutil.copy2(override_manifest_file, test_config_dict['workload_home_dir'])
        if buildForThroughput == True:
            tmp_original_file = os.path.join(test_config_dict['workload_home_dir'], os.path.basename(test_config_dict['throughput_manifest_file']))
        elif buildForThroughput == False:
            tmp_original_file = os.path.join(test_config_dict['workload_home_dir'], os.path.basename(test_config_dict['latency_manifest_file']))
        os.rename(tmp_original_file, original_manifest_file)

        return True


    def build_workload(self, test_config_dict, buildForThroughput = True):
        if buildForThroughput:
            if not self.replace_manifest_file(test_config_dict, True):
                print("\n-- Failure: Workload build failure. Returning without building the workload.")
                return False
        else:
            if not self.replace_manifest_file(test_config_dict, False):
                print("\n-- Failure: Workload build failure. Returning without building the workload.")
                return False
        
        cwd = os.getcwd()

        # Not checking for existence of model, as we may be building it for either throughput or latency.
        # So, this method can be invoked from the caller based on whether we are building the
        # workload for throughput or latency.

        os.chdir(test_config_dict['workload_home_dir'])

        setupvars_path = os.path.join(test_config_dict['workload_home_dir'], 'openvino_2021/bin', 'setupvars.sh')
        
        if os.path.exists(setupvars_path):
            print(f"\n-- Building workload model '{test_config_dict['model_name']}'..\n")
            ov_env_var_bld_cmd = f"bash -c 'source {setupvars_path} && make SGX=1'"
            print("\n-- Setting up OpenVINO environment variables and building Openvino..\n", ov_env_var_bld_cmd)
            subprocess.run(ov_env_var_bld_cmd, shell=True, check=True)
        else:
            os.chdir(cwd)
            print(f"\n-- Failure: OpenVino not installed in {setupvars_path}. Returning without building the workload.\n")
            return False

        # After the build is complete we need to del the existing overridden manifest and rename
        # manifest.ori to the expected original name by the workload.
        original_manifest_file = os.path.join(test_config_dict['workload_home_dir'], test_config_dict['original_manifest_file'])
        os.remove(original_manifest_file)
        os.rename(original_manifest_file+'.ori', original_manifest_file)

        os.chdir(cwd)

        # Check for build status by verifying the existence of model.xml file and return accordingly.
        model_file_path = os.path.join(test_config_dict['model_dir'],test_config_dict['fp'][0],test_config_dict['model_name']+'.xml')
        if os.path.exists(model_file_path):
            print("\n-- Model is built. Can proceed further to execute the workload..")
            return True
        else:
            print("\n-- Failure: Model not found/built. Returning without building the model.")
            return False


    def pre_actions(self, test_config_dict):
        utils.set_cpu_freq_scaling_governor()        
        self.install_workload(test_config_dict)
        

    def execute_workload(self, test_config_dict, execForThroughput = True):
        pass

    def __init__(self, test_config_dict):
        # result is the overall score for all benchmarks
        # it is a list containing [baseline_score, testapp_score, percent_degradaton]
        self.result = []
        # self.records are the individual benchmark results
        # {benchmark, [baseline_score, testapp_score, percent_degradaton]}
        self.records = collections.OrderedDict()
        time.sleep(1)
        
    def post_actions(self, TEST_CONFIG):
        pass

    def get_command(self, TEST_CONFIG):
        pass

    # calculate the percent degradation
    @staticmethod
    def percent_degradation(baseline, testapp):
        return '{:0.3f}'.format(100 * (float(baseline) - float(testapp)) / float(baseline))

    def parse_performance(self, TEST_CONFIG):
        pass
    

WORKLOAD = OpenvinoWorkload(test_config_dict = None)

#eof