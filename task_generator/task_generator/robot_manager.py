# from math import ceil, sqrt
import math
import yaml
import os
import threading
from typing import Union
import rospy
import tf
from flatland_msgs.srv import MoveModel, MoveModelRequest
from flatland_msgs.srv import StepWorld
from geometry_msgs.msg import Pose2D, PoseWithCovarianceStamped, PoseStamped

from nav_msgs.msg import OccupancyGrid, Path

from .utils import generate_freespace_indices, get_random_pos_on_map


class RobotManager:
    """
    A manager class using flatland provided services to spawn, move and delete Robot. Currently only one robot
    is managed
    """

    def __init__(self, map_: OccupancyGrid, robot_yaml_path: str, is_training_mode: bool):
        """[summary]

        Args:
            map_ (OccupancyGrid): the map info
            robot_yaml_path (str): the file name of the robot yaml file.
            is_training_mode (bool): a flag to indicate the mode (training or test)
        """
        self.is_training_mode = is_training_mode
        self._get_robot_configration(robot_yaml_path)
        # setup proxy to handle  services provided by flatland
        rospy.wait_for_service('move_model', timeout=20)
        #rospy.wait_for_service('step_world', timeout=20)
        self._srv_move_model = rospy.ServiceProxy('move_model', MoveModel)
        # it's only needed in training mode to send the clock signal.
        self._step_world = rospy.ServiceProxy("step_world", StepWorld)

        # subcriber
        # self._global_path_sub = rospy.Subscriber(
        #     "move_base/NavfnROS/plan", Path, self._global_path_callback)
        # self._goal_status_sub = rospy.Subscriber("move_base/status", GoalStatusArray,
        #                                          self.goal_status_callback, queue_size=1)

        # publisher
        # publish the start position of the robot
        # self._initialpose_pub = rospy.Publisher(
        #     'initialpose', PoseWithCovarianceStamped, queue_size=1)
        self._goal_pub = rospy.Publisher(
            '/goal', PoseStamped, queue_size=1, latch=True)

        self.update_map(map_)

        # path generated by the  global planner
        self._global_path = Path()
        # the timestamp will be used for checking whether the global planner can find a valid path
        # between new start position and new goal
        self._old_global_path_timestamp = None
        self._new_global_path_generated = False
        # a condition variable used for
        self._global_path_con = threading.Condition()
        self._static_obstacle_name_list = []

    def _get_robot_configration(self, robot_yaml_path):
        """get robot info e.g robot name, radius, Laser related infomation

        Args:
            robot_yaml_path ([type]): [description]
        """
        self.ROBOT_NAME = os.path.basename(robot_yaml_path).split('.')[0]
        with open(robot_yaml_path, 'r') as f:
            robot_data = yaml.safe_load(f)
            # get robot radius
            for body in robot_data['bodies']:
                if body['name'] == "base_footprint":
                    for footprint in body['footprints']:
                        if footprint['type'] == 'circle':
                            self.ROBOT_RADIUS = footprint.setdefault('radius', 0.2)
            # get laser_update_rate
            for plugin in robot_data['plugins']:
                if plugin['type'] == 'Laser':
                    self.LASER_UPDATE_RATE = plugin.setdefault('update_rate', 1)

    def update_map(self, new_map: OccupancyGrid):
        self.map = new_map
        # a tuple stores the indices of the non-occupied spaces. format ((y,....),(x,...)
        self._free_space_indices = generate_freespace_indices(self.map)

    def move_robot(self, pose: Pose2D):
        """move the robot to a given position

        Args:
            pose (Pose2D): target postion
        """
        # call service move_model

        srv_request = MoveModelRequest()
        srv_request.name = self.ROBOT_NAME
        srv_request.pose = pose

        # call service
        self._srv_move_model(srv_request)
        if self.is_training_mode:
            # a necessaray procedure to let the flatland publish the
            # laser,odom's Transformation, which are needed for creating
            # global path
            for _ in range(self.LASER_UPDATE_RATE + 1):
                self._step_world()

        # publish robot position
        # self._pub_initial_position(pose.x, pose.y, pose.theta)

    def set_start_pos_random(self):
        start_pos = Pose2D()
        start_pos.x, start_pos, start_pos.theta = get_random_pos_on_map(
            self._free_space_indices, self.map, self.ROBOT_RADIUS)
        self.move_robot(start_pos)

    def set_start_pos_goal_pos(self, start_pos: Union[Pose2D, None]
                               = None, goal_pos: Union[Pose2D, None] = None, min_dist=1):
        """set up start position and the goal postion. Path validation checking will be conducted. If it failed, an
        exception will be raised.

        Args:
            start_pos (Union[Pose2D,None], optional): start position. if None, it will be set randomly. Defaults to None.
            goal_pos (Union[Pose2D,None], optional): [description]. if None, it will be set randomly .Defaults to None.
            min_dist (float): minimum distance between start_pos and goal_pos
        Exception:
            Exception("can not generate a path with the given start position and the goal position of the robot")
        """

        def dist(x1, y1, x2, y2):
            return math.sqrt((x1 - x2)**2 + (y1 - y2)**2)

        if start_pos is None or goal_pos is None:
            # if any of them need to be random generated, we set a higher threshold,otherwise only try once
            max_try_times = 20
        else:
            max_try_times = 1

        i_try = 0
        start_pos_ = None
        goal_pos_ = None
        while i_try < max_try_times:

            if start_pos is None:
                start_pos_ = Pose2D()
                start_pos_.x, start_pos_.y, start_pos_.theta = get_random_pos_on_map(
                    self._free_space_indices, self.map, self.ROBOT_RADIUS * 2)
            else:
                start_pos_ = start_pos
            if goal_pos is None:
                goal_pos_ = Pose2D()
                goal_pos_.x, goal_pos_.y, goal_pos_.theta = get_random_pos_on_map(
                    self._free_space_indices, self.map, self.ROBOT_RADIUS * 4)
            else:
                goal_pos_ = goal_pos

            if dist(start_pos_.x, start_pos_.y, goal_pos_.x, goal_pos_.y) < min_dist:
                i_try += 1
                continue
            # move the robot to the start pos
            self.move_robot(start_pos_)
            try:
                # publish the goal, if the gobal plath planner can't generate a path, a, exception will be raised.
                self.publish_goal(goal_pos_.x, goal_pos_.y, goal_pos_.theta)
                break
            except rospy.ServiceException:
                i_try += 1
        if i_try == max_try_times:
            # TODO Define specific type of Exception
            raise rospy.ServiceException(
                "can not generate a path with the given start position and the goal position of the robot")
        else:
            return start_pos_, goal_pos_

    def _validate_path(self):
        """ after publish the goal, the global planner should publish path. If it's not published within 0.1s, an exception will
        be raised.

        Raises:
            Exception: [description]
        """

        with self._global_path_con:
            self._global_path_con.wait_for(
                predicate=self._new_global_path_generated, timeout=0.1)
            if not self._new_global_path_generated:
                raise rospy.ServiceException(
                    "can not generate a path with the given start position and the goal position of the robot")
            else:
                self._new_global_path_generated = False  # reset it

    def _pub_initial_position(self, x, y, theta):
        """
        Publishing new initial position (x, y, theta) --> for localization
        :param x x-position of the robot
        :param y y-position of the robot
        :param theta theta-position of the robot
        """
        initpose = PoseWithCovarianceStamped()
        initpose.header.stamp = rospy.get_rostime()
        initpose.header.frame_id = "map"
        initpose.pose.pose.position.x = x
        initpose.pose.pose.position.y = y
        quaternion = tf.transformations.quaternion_from_euler(0, 0, theta)

        initpose.pose.pose.orientation.w = quaternion[0]
        initpose.pose.pose.orientation.x = quaternion[1]
        initpose.pose.pose.orientation.y = quaternion[2]
        initpose.pose.pose.orientation.z = quaternion[3]
        self._initialpose_pub.publish(initpose)

    def publish_goal(self, x, y, theta):
        """
        Publishing goal (x, y, theta)
        :param x x-position of the goal
        :param y y-position of the goal
        :param theta theta-position of the goal
        """
        self._old_global_path_timestamp = self._global_path.header.stamp
        goal = PoseStamped()
        goal.header.stamp = rospy.get_rostime()
        goal.header.frame_id = "map"
        goal.pose.position.x = x
        goal.pose.position.y = y
        quaternion = tf.transformations.quaternion_from_euler(0, 0, 0)
        goal.pose.orientation.w = quaternion[0]
        goal.pose.orientation.x = quaternion[1]
        goal.pose.orientation.y = quaternion[2]
        goal.pose.orientation.z = quaternion[3]
        self._goal_pub.publish(goal)
        # self._validate_path()

    def _global_path_callback(self, global_path: Path):
        with self._global_path_con:
            self._global_path = global_path
            if self._old_global_path_timestamp is None or global_path.header.stamp > self._old_global_path_timestamp:
                self._new_global_path_generated = True
            self._global_path_con.notify()

    def __mean_square_dist_(self, x, y):
        return math.sqrt(math.pow(x, 2) + math.pow(y, 2))
