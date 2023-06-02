import sys
import time
import re
import statistics
from common.config_files.constants import *
from common.libs import utils
from baremetal_benchmarking import gramine_libs
from conftest import trd


class TensorflowWorkload():
    def __init__(self, test_config_dict):
        self.workload_home_dir = os.path.join(FRAMEWORK_HOME_DIR, test_config_dict['workload_home_dir'])
        os.makedirs(self.workload_home_dir, exist_ok=True)
        self.command = None

    def get_workload_home_dir(self):
        return self.workload_home_dir
        
    def download_workload(self):
        if sys.version_info < (3, 6):
            raise Exception("Please upgrade Python version to atleast 3.6 or higher before executing this workload.")

        print("\n-- Executing pip upgrade command..")
        utils.exec_shell_cmd(PIP_UPGRADE_CMD)
        
        print("\n-- Installing Tensorflow..")
        utils.exec_shell_cmd(TENSORFLOW_INSTALL_CMD)
        
        print("\n-- Downgrading Markupsafe to v2.0.1.")
        markupsafe_cmd = "pip3 install markupsafe==2.0.1"
        utils.exec_shell_cmd(markupsafe_cmd, None)

    def download_bert_models(self):
        if not os.path.exists('./models'):
            utils.exec_shell_cmd(TF_BERT_INTEL_AI_MODELS_CLONE_CMD, None)
        
        os.makedirs('./data', exist_ok=True)

        dataset_folder_name = TF_BERT_DATASET_UNZIP_CMD.split()[1].split('.')[0]
        if not os.path.exists(dataset_folder_name):
            print("\n-- Downloading BERT dataset models..")
            utils.exec_shell_cmd(TF_BERT_DATASET_WGET_CMD, None)
            utils.exec_shell_cmd(TF_BERT_DATASET_UNZIP_CMD, None)
            utils.exec_shell_cmd(TF_BERT_SQUAAD_DATASET_WGET_CMD, None)
        
        checkpoints_folder_name = TF_BERT_CHECKPOINTS_UNZIP_CMD.split()[1].split('.')[0]
        if not os.path.exists(checkpoints_folder_name):
            print("\n-- Downloading BERT checkpoints models..")
            utils.exec_shell_cmd(TF_BERT_CHECKPOINTS_WGET_CMD, None)
            utils.exec_shell_cmd(TF_BERT_CHECKPOINTS_UNZIP_CMD, None)

        print("\n-- Downloading BERT FP32 model..")
        utils.exec_shell_cmd(TF_BERT_FP32_MODEL_WGET_CMD, None)
        
        print("\n-- Required BERT models downloaded..")

    def download_resnet_models(self):
        if not os.path.exists('./models'):
            print("\n-- Downloading RESNET Intel_AI models..")
            utils.exec_shell_cmd(TF_RESNET_INTEL_AI_MODELS_CLONE_CMD, None)
            # Until r2.9 branch of Resnet IntelAI models repo, inference was
            # performed only on int8 precision.
            # When the Resnet command is executed, it looks out for a file
            # 'eval_image_classifier_inference_weight_sharing.py' to be present
            # in below path.
            # "models/image_recognition/tensorflow/resnet50v1_5/inference/"
            # Now, with the latest branch (r2.11) of Resnet IntelAI models repo,
            # inference is performed on few additional precisions and the above
            # mentioned file is added for all precisions inside respective folders.
            # Until dev team informs us as to what precision to run on, we will
            # execute perf runs by checking out the older branch (r2.9) of the repo.
            os.chdir('models')
            print("\n-- Checking out RESNET models r2.9 branch..")
            utils.exec_shell_cmd("git checkout r2.9", None)
            os.chdir(self.workload_home_dir)

        print("\n-- Downloading RESNET Pretrained model..")
        utils.exec_shell_cmd(TF_RESNET_INT8_MODEL_WGET_CMD, None)

        print("\n-- Required RESNET models downloaded..")

    def build_and_install_workload(self, test_config_dict):
        print("\n###### In build_and_install_workload #####\n")

        if test_config_dict['model_name'] == 'bert':
            self.download_bert_models()
        elif test_config_dict['model_name'] == 'resnet':
            self.download_resnet_models()
        else:
            raise Exception("Unknown tensorflow model. Please check the test yaml file.")

    def generate_manifest(self):
        entrypoint_path = utils.exec_shell_cmd("sh -c 'command -v python3'")
        pythondist_path = os.path.expanduser('~/.local/lib/python') + '%d.%d' % sys.version_info[:2] + "/site-packages"

        manifest_cmd = "gramine-manifest -Dlog_level={} -Darch_libdir={} -Dentrypoint={} -Dpythondistpath={} \
                            python.manifest.template > python.manifest".format(
            LOG_LEVEL, os.environ.get('ARCH_LIBDIR'), entrypoint_path, pythondist_path)
        print("\n-- Generating manifest..\n", manifest_cmd)

        utils.exec_shell_cmd(manifest_cmd)

    def install_mimalloc(self):
        if os.path.exists(MIMALLOC_INSTALL_PATH):
            print("\n-- Library 'mimalloc' already exists.. Returning without rebuilding.\n")
            return

        print("\n-- Setting up mimalloc for Openvino..\n", MIMALLOC_CLONE_CMD)
        utils.exec_shell_cmd(MIMALLOC_CLONE_CMD)
        mimalloc_dir = os.path.join(self.workload_home_dir, 'mimalloc')
        os.chdir(mimalloc_dir)
        mimalloc_make_dir_path = os.path.join(mimalloc_dir, 'out/release')
        os.makedirs(mimalloc_make_dir_path, exist_ok=True)
        os.chdir(mimalloc_make_dir_path)

        utils.exec_shell_cmd("cmake ../..")
        utils.exec_shell_cmd("make")
        utils.exec_shell_cmd("sudo make install")

        os.chdir(self.workload_home_dir)

        if not os.path.exists(MIMALLOC_INSTALL_PATH):
            raise Exception(f"\n-- Library {MIMALLOC_INSTALL_PATH} not generated/installed.\n")

    def encrypt_models(self, test_config_dict, enc_key):
        os.makedirs("./encrypted_models", exist_ok=True)
        if test_config_dict['model_name'] == 'bert':
            try: # Deleting old model if existing.
                os.remove("encrypted_models/" + TF_BERT_FP32_MODEL_NAME)
            except OSError:
                pass
            # We need to provide the absolute path of the output encrypted file within the below encrypt command.
            encrypted_file = os.path.join(FRAMEWORK_HOME_DIR, test_config_dict['workload_home_dir'], "encrypted_models", TF_BERT_FP32_MODEL_NAME)
            encrypt_cmd = f"gramine-sgx-pf-crypt encrypt -w {enc_key} -i data/{TF_BERT_FP32_MODEL_NAME} -o {encrypted_file}"
            print("\n-- Encrypting BERT model..\n", encrypt_cmd)
        elif test_config_dict['model_name'] == 'resnet':
            try: # Deleting old model if existing.
                os.remove("encrypted_models/" + TF_RESNET_INT8_MODEL_NAME)
            except OSError:
                pass
            # We need to provide the absolute path of the output encrypted file within the below encrypt command.
            encrypted_file = os.path.join(FRAMEWORK_HOME_DIR, test_config_dict['workload_home_dir'], "encrypted_models", TF_RESNET_INT8_MODEL_NAME)
            encrypt_cmd = f"gramine-sgx-pf-crypt encrypt -w {enc_key} -i {TF_RESNET_INT8_MODEL_NAME} -o {encrypted_file}"
            print("\n-- Encrypting RESNET model..\n", encrypt_cmd)
        else:
            raise Exception("Unknown tensorflow model for encryption! Please check the test yaml file..")
        
        utils.exec_shell_cmd(encrypt_cmd, None)

    def update_manifest_entries(self, test_config_dict, hex_enc_key_dump):
        manifest_filename = test_config_dict['manifest_name'] + ".manifest.template"
        if test_config_dict['model_name'] == 'bert':
            model_name = TF_BERT_FP32_MODEL_NAME
        elif test_config_dict['model_name'] == 'resnet':
            model_name = TF_RESNET_INT8_MODEL_NAME
        else:
            raise Exception("Unknown tensorflow model. Please check the test yaml file.")
        
        encrypted_file = os.path.join(FRAMEWORK_HOME_DIR, test_config_dict['workload_home_dir'], "encrypted_models", model_name)
        search_str = "# encrypted file mount"
        replace_str = "{ type = \"encrypted\", path = \"" + encrypted_file + "\", uri = \"file:" + encrypted_file + "\" },"
        enc_file_mount_cmd = f"sed -i 's|{search_str}|{replace_str}|' {manifest_filename}"
        utils.exec_shell_cmd(enc_file_mount_cmd, None)
        search_str = "# encrypted insecure__keys"
        replace_str = "fs.insecure__keys.default = \"" + hex_enc_key_dump + "\""
        enc_insecure_keys_cmd = f"sed -i 's|{search_str}|{replace_str}|' {manifest_filename}"
        utils.exec_shell_cmd(enc_insecure_keys_cmd, None)
        search_str = "# encrypted allowed file"
        replace_str = "\"file:" + encrypted_file + "\","
        enc_allowed_file_cmd = f"sed -i 's|{search_str}|{replace_str}|' {manifest_filename}"
        utils.exec_shell_cmd(enc_allowed_file_cmd, None)

    def pre_actions(self, test_config_dict):
        os.chdir(self.get_workload_home_dir())
        utils.set_threads_cnt_env_var()
        utils.set_cpu_freq_scaling_governor()
        if test_config_dict['model_name'] == 'bert':
            self.install_mimalloc()
        gramine_libs.update_manifest_file(test_config_dict)

    def setup_workload(self, test_config_dict):
        self.download_workload()
        self.build_and_install_workload(test_config_dict)
        if os.environ['encryption'] == '1':
            hex_enc_key_dump, enc_key = utils.gen_encryption_key()
            self.update_manifest_entries(test_config_dict, hex_enc_key_dump)
            self.encrypt_models(test_config_dict, enc_key)
        self.generate_manifest()
        gramine_libs.generate_sgx_token_and_sig(test_config_dict)

    def construct_workload_exec_cmd(self, test_config_dict, input_model_file, exec_mode = 'native', iteration=1):
        tf_exec_cmd = None
        exec_mode_cmd = 'python3' if exec_mode == 'native' else exec_mode + ' python'
        taskset_str = f"0-{int(os.environ['CORES_PER_SOCKET']) - 1} "
        output_file_name = LOGS_DIR + "/" + test_config_dict['test_name'] + '_' + exec_mode + '_' + str(iteration) + '.log'

        print("\nOutput file name = ", output_file_name)
        if test_config_dict['model_name'] == 'bert':
            tf_exec_cmd = "OMP_NUM_THREADS=" + os.environ['CORES_PER_SOCKET'] + " KMP_AFFINITY=granularity=fine,verbose,compact,1,0" + \
                            " taskset -c " + taskset_str + exec_mode_cmd + \
                            " models/models/language_modeling/tensorflow/bert_large/inference/run_squad.py" + \
                            " --init_checkpoint=data/bert_large_checkpoints/model.ckpt-3649" + \
                            " --vocab_file=data/wwm_uncased_L-24_H-1024_A-16/vocab.txt" + \
                            " --bert_config_file=data/wwm_uncased_L-24_H-1024_A-16/bert_config.json" + \
                            " --predict_file=data/wwm_uncased_L-24_H-1024_A-16/dev-v1.1.json" + \
                            " --precision=int8" + \
                            " --output_dir=output/bert-squad-output" + \
                            " --predict_batch_size=" + str(test_config_dict['batch_size']) + \
                            " --experimental_gelu=True" + \
                            " --optimized_softmax=True" + \
                            " --input_graph=" + input_model_file + \
                            " --do_predict=True --mode=benchmark" + \
                            " --inter_op_parallelism_threads=1" + \
                            " --intra_op_parallelism_threads=" + os.environ['CORES_PER_SOCKET'] + " | tee " + output_file_name

            os.environ['LD_PRELOAD'] = MIMALLOC_INSTALL_PATH if exec_mode == 'native' else ''

        elif test_config_dict['model_name'] == 'resnet':
            tf_exec_cmd = "OMP_NUM_THREADS=" + os.environ['CORES_PER_SOCKET'] + " KMP_AFFINITY=granularity=fine,verbose,compact,1,0" + \
                            " taskset -c " + taskset_str + exec_mode_cmd + \
                            " models/models/image_recognition/tensorflow/resnet50v1_5/inference/eval_image_classifier_inference.py" + \
                            " --input-graph=" + input_model_file + \
                            " --num-inter-threads=1" + \
                            " --num-intra-threads=" + os.environ['CORES_PER_SOCKET'] + \
                            " --batch-size=" + str(test_config_dict['batch_size']) + \
                            " --warmup-steps=50" + \
                            " --steps=500 | tee " + output_file_name

            os.environ['LD_PRELOAD'] = TCMALLOC_INSTALL_PATH if exec_mode == 'native' else ''

        else:
            raise Exception("\n-- Failure: Internal error! Non-existent tensorflow model..")

        print("\nCommand name = ", tf_exec_cmd)
        return tf_exec_cmd

    @staticmethod
    def get_metric_value(test_config_dict, test_file_name):
        with open(test_file_name, 'r') as test_fd:
            for line in test_fd:
                if re.search(test_config_dict['metric'], line, re.IGNORECASE) is not None:
                    throughput = re.findall('\d+\.\d+', line)
                    return round(float(throughput[0]),3)

    # Build the workload execution command based on execution params and execute it.
    def execute_workload(self, tcd, e_mode, test_dict):
        print("\n##### In execute_workload #####\n")

        if os.environ['encryption'] == '1' and e_mode == 'gramine-sgx':
            if tcd['model_name'] == 'bert':
                input_model_file = os.path.join(FRAMEWORK_HOME_DIR, tcd['workload_home_dir'], "encrypted_models", TF_BERT_FP32_MODEL_NAME)
            elif tcd['model_name'] == 'resnet':
                input_model_file = os.path.join(FRAMEWORK_HOME_DIR, tcd['workload_home_dir'], "encrypted_models", TF_RESNET_INT8_MODEL_NAME)
            else:
                raise Exception("Unknown tensorflow model. Please check the test yaml file.")
        else:
            if tcd['model_name'] == 'bert':
                input_model_file = "data/" + TF_BERT_FP32_MODEL_NAME
            elif tcd['model_name'] == 'resnet':
                input_model_file = TF_RESNET_INT8_MODEL_NAME
            else:
                raise Exception("Unknown tensorflow model. Please check the test yaml file.")

        for j in range(tcd['iterations']):
            self.command = self.construct_workload_exec_cmd(tcd, input_model_file, e_mode, j + 1)

            if self.command is None:
                raise Exception(
                    f"\n-- Failure: Unable to construct command for {tcd['test_name']} Exec_mode: {e_mode}")

            cmd_output = utils.exec_shell_cmd(self.command)
            print(cmd_output)
            if cmd_output is None or utils.verify_output(cmd_output, tcd['metric']) is None:
                raise Exception(
                    f"\n-- Failure: Test workload execution failed for {tcd['test_name']} Exec_mode: {e_mode}")

            test_file_name = LOGS_DIR + '/' + tcd['test_name'] + '_' + e_mode + '_' + str(j+1) + '.log'
            if not os.path.exists(test_file_name):
                raise Exception(f"\nFailure: File {test_file_name} does not exist for parsing performance..")
            metric_val = float(self.get_metric_value(tcd, test_file_name))
            test_dict[e_mode].append(metric_val)
            
            time.sleep(TEST_SLEEP_TIME_BW_ITERATIONS)

    def update_test_results_in_global_dict(self, tcd, test_dict):
        global trd
        if 'native' in tcd['exec_mode']:
            test_dict['native-avg'] = '{:0.3f}'.format(statistics.median(test_dict['native']))

        if 'gramine-direct' in tcd['exec_mode']:
            test_dict['direct-avg'] = '{:0.3f}'.format(statistics.median(test_dict['gramine-direct']))
            if 'native' in tcd['exec_mode']:
                test_dict['direct-deg'] = utils.percent_degradation(tcd, test_dict['native-avg'], test_dict['direct-avg'])

        if 'gramine-sgx' in tcd['exec_mode']:
            test_dict['sgx-avg'] = '{:0.3f}'.format(statistics.median(test_dict['gramine-sgx']))
            if 'native' in tcd['exec_mode']:
                test_dict['sgx-deg'] = utils.percent_degradation(tcd, test_dict['native-avg'], test_dict['sgx-avg'])

        utils.write_to_csv(tcd, test_dict)

        trd[tcd['workload_name']] = trd.get(tcd['workload_name'], {})
        trd[tcd['workload_name']].update({tcd['test_name']: test_dict})
