import os
import rospy
import csv

from datetime import datetime as dt

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import SubprocVecEnv, DummyVecEnv, VecNormalize
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import EvalCallback

from task_generator.task_generator.tasks import get_predefined_task
from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.scripts.custom_policy import *
from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.rl_agent.envs.flatland_gym_env import FlatlandEnv
from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.tools.argsparser import parse_training_args
from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.tools.train_agent_utils import *
from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.tools.custom_mlp_utils import *
from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.tools.staged_train_callback import InitiateNewTrainStage

##### HYPERPARAMETER #####
""" will be used upon initializing new agent """
robot = "myrobot"
gamma = 0.99
n_steps = 128
ent_coef = 0.01
learning_rate = 2.5e-4
vf_coef = 0.5
max_grad_norm = 0.5
gae_lambda = 0.95
batch_size = 64
n_epochs = 4
clip_range = 0.2
reward_fnc = "rule_01"
discrete_action_space = False
normalize = True
start_stage = 1
task_mode = "staged"    # custom, random or staged
normalize = True
##########################



def change_noise(noise_parameter):

    with open("noise_parameter",'w') as noise_data:
        Noise_writer = csv.writer(noise_data)
        Noise_writer.writerow([noise_parameter])
        noise_data.close()

def get_agent_name(args):
    """ Function to get agent name to save to/load from file system
    
    Example names:
    "MLP_B_64-64_P_32-32_V_32-32_relu_2021_01_07__10_32"
    "DRL_LOCAL_PLANNER_2021_01_08__7_14"

    :param args (argparse.Namespace): Object containing the program arguments
    """
    START_TIME = dt.now().strftime("%Y_%m_%d__%H_%M")

    if args.custom_mlp:
        return "MLP_B_" + args.body + "_P_" + args.pi + "_V_" + args.vf + "_" + args.act_fn + "_" + START_TIME
    if args.load is None:
        return args.agent + "_" + START_TIME
    return args.load


def get_paths(agent_name: str, args) -> dict:
    """ Function to generate agent specific paths 
    
    :param agent_name: Precise agent name (as generated by get_agent_name())
    :param args (argparse.Namespace): Object containing the program arguments
    """
    dir = rospkg.RosPack().get_path('arena_local_planner_drl')

    PATHS = {
        'model' : os.path.join(dir, 'agents', agent_name),
        'tb' : os.path.join(dir, 'training_logs', 'tensorboard', agent_name),
        'eval' : os.path.join(dir, 'training_logs', 'train_eval_log', agent_name),
        'robot_setting' : os.path.join(rospkg.RosPack().get_path('simulator_setup'), 'robot', robot + '.model.yaml'),
        'robot_as' : os.path.join(rospkg.RosPack().get_path('arena_local_planner_drl'), 'configs', 'default_settings.yaml'),
        'curriculum' : os.path.join(rospkg.RosPack().get_path('arena_local_planner_drl'), 'configs', 'training_curriculum.yaml')
    }
    # check for mode
    if args.load is None:
        os.makedirs(PATHS.get('model'))
    else:
        if not os.path.isfile(os.path.join(PATHS.get('model'), AGENT_NAME + ".zip")) and not os.path.isfile(os.path.join(PATHS.get('model'), "best_model.zip")):
            raise FileNotFoundError("Couldn't find model named %s.zip' or 'best_model.zip' in '%s'" % (AGENT_NAME, PATHS.get('model')))
    # evaluation log enabled
    if args.eval_log:
        if not os.path.exists(PATHS.get('eval')):
            os.makedirs(PATHS.get('eval'))
    else:
        PATHS['eval'] = None
    # tensorboard log enabled
    if args.tb:
        if not os.path.exists(PATHS.get('tb')):
            os.makedirs(PATHS.get('tb'))
    else:
        PATHS['tb'] = None

    return PATHS



if __name__ == "__main__":
    args, _ = parse_training_args()

    rospy.init_node("train_node")

    # generate agent name and model specific paths
    AGENT_NAME = get_agent_name(args)
    PATHS = get_paths(AGENT_NAME, args)

    #print("________ STARTING TRAINING WITH:  %s ________\n" % AGENT_NAME)

    # initialize hyperparameters (save to/ load from json)
    hyperparams_obj = agent_hyperparams(
        AGENT_NAME, robot, gamma, n_steps, ent_coef, learning_rate, vf_coef,max_grad_norm, gae_lambda, batch_size, 
        n_epochs, clip_range, reward_fnc, discrete_action_space, normalize, task_mode, start_stage)
    params = initialize_hyperparameters(agent_name=AGENT_NAME, PATHS=PATHS, hyperparams_obj=hyperparams_obj, load_target=args.load)

    # instantiate gym environment
    n_envs = 1
    task_manager = get_predefined_task(params['task_mode'], params['curr_stage'], PATHS)
    env = DummyVecEnv(
        [lambda: FlatlandEnv(task_manager, PATHS.get('robot_setting'), PATHS.get('robot_as'), params['reward_fnc'], params['discrete_action_space'], goal_radius=1.00, max_steps_per_episode=200)] * n_envs)
    if params['normalize']:
        env = VecNormalize(env, training=True, norm_obs=True, norm_reward=False, clip_reward=15)

    # instantiate eval environment
    trainstage_cb = InitiateNewTrainStage(TaskManager=task_manager, TreshholdType="rew", rew_threshold=14.5, task_mode=params['task_mode'], verbose=1)
    eval_env = Monitor(FlatlandEnv(
        task_manager, PATHS.get('robot_setting'), PATHS.get('robot_as'), params['reward_fnc'], params['discrete_action_space'], goal_radius=1.00, max_steps_per_episode=250),
        PATHS.get('eval'), info_keywords=("done_reason",))
    eval_env = DummyVecEnv([lambda: eval_env])
    if params['normalize']:
        eval_env = VecNormalize(eval_env, training=False, norm_obs=True, norm_reward=False, clip_reward=15)
    eval_cb = EvalCallback(
        eval_env, n_eval_episodes=20, eval_freq=15000, log_path=PATHS.get('eval'), best_model_save_path=PATHS.get('model'), deterministic=True, callback_on_new_best=trainstage_cb)

    # determine mode
    if args.custom_mlp:
        # custom mlp flag
        model = PPO("MlpPolicy", env, policy_kwargs = dict(net_arch = args.net_arch, activation_fn = get_act_fn(args.act_fn)), 
                    gamma = gamma, n_steps = n_steps, ent_coef = ent_coef, learning_rate = learning_rate, vf_coef = vf_coef, 
                    max_grad_norm = max_grad_norm, gae_lambda = gae_lambda, batch_size = batch_size, n_epochs = n_epochs, clip_range = clip_range, 
                    tensorboard_log = PATHS.get('tb'), verbose = 1)
    elif args.agent is not None:
        # predefined agent flag
        if args.agent == "MLP_ARENA2D":
                model = PPO(MLP_ARENA2D_POLICY, env, gamma = gamma, n_steps = n_steps, ent_coef = ent_coef, 
                        learning_rate = learning_rate, vf_coef = vf_coef, max_grad_norm = max_grad_norm, gae_lambda = gae_lambda, 
                        batch_size = batch_size, n_epochs = n_epochs, clip_range = clip_range, tensorboard_log = PATHS.get('tb'), verbose = 1)

        elif args.agent == "DRL_LOCAL_PLANNER" or args.agent == "CNN_NAVREP":
            if args.agent == "DRL_LOCAL_PLANNER":
                policy_kwargs = policy_kwargs_drl_local_planner
            else:
                policy_kwargs = policy_kwargs_navrep

            model = PPO("CnnPolicy", env, policy_kwargs = policy_kwargs, 
                gamma = gamma, n_steps = n_steps, ent_coef = ent_coef, learning_rate = learning_rate, vf_coef = vf_coef, 
                max_grad_norm = max_grad_norm, gae_lambda = gae_lambda, batch_size = batch_size, n_epochs = n_epochs, 
                clip_range = clip_range, tensorboard_log = PATHS.get('tb'), verbose = 1)
    else:
        # load flag
        if os.path.isfile(os.path.join(PATHS.get('model'), AGENT_NAME + ".zip")):
            model = PPO.load(os.path.join(PATHS.get('model'), AGENT_NAME), env)
        elif os.path.isfile(os.path.join(PATHS.get('model'), "best_model.zip")):
            model = PPO.load(os.path.join(PATHS.get('model'), "best_model"), env)


    #model.save(os.path.join(PATHS.get('model'), AGENT_NAME))
    noise = 1
    print("test the trained DRL agent!")
    with open("result_with_noise",'w') as Result_data:
            Result_data.close()
            
    change_noise(noise)
            
    while(noise < 12):
        obs = env.reset()
        count,success,no_result = 0,0,0
        while count < 100:
            
            action, _state = model.predict(obs, deterministic = True)
            obs, reward, done, info = env.step(action)
    
            #env.render()
            if done:
                obs = env.reset()
                
                print(info[0]['done_reason'])
                if info[0]['done_reason'] == 2:
                    success += 1
                elif info[0]['done_reason'] == 0:
                    no_result += 1
                count += 1
        print("total test of %d times, successes %d,  no results %d,  failures %d. \n Success rate: %.3f, failure rate: %.3f."
              %(count,success,no_result,count-success-no_result,success/count,1 - success/count))
        
        with open("result_with_noise",'a+') as Result_data:
                Reslut_writer = csv.writer(Result_data)
                Reslut_writer.writerow([count,success,no_result,count-success-no_result,success/count,1 - success/count])
                Result_data.close()
        noise += 1
        change_noise(noise)
        
"""
    s = time.time()
    model.learn(total_timesteps = 3000)
    print("steps per second: {}".format(1000 / (time.time() - s)))
    # obs = env.reset()
    # for i in range(1000):
    #     action, _state = model.predict(obs, deterministic = True)
    #     obs, reward, done, info = env.step(action)
    #     env.render()
    #     if done:
    #       obs = env.reset()
"""