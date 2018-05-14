
'''
    From here the Robot can be controlled by an AI, internal or external through a message system
    (Redis and/or PEDRO)
'''

from RobotModel import PioneerP3DX, VRep
import time
from math import tanh, log
from redis import Redis
from pedroclient import *


def invoke(obj, method_string):
    methL=  method_string.split('(')
    method_name = methL[0]
    args = float(methL[1])
    # method = getattr(obj.__class__, method_name)
    # print('invoking: ', method, args)
    try:
        getattr(obj.__class__, method_name)(obj, args)
    except AttributeError:
        raise NotImplementedError("Class `{}` does not implement `{}`".format(obj.__class__.__name__, method_name))

def demo_control():
    with VRep.connect("127.0.0.1", 19999) as api:
        r = PioneerP3DX('Pioneer_p3dx', api)
        speed = 0.0
        while True:
            rl = r.right_length()
            ll = r.left_length()
            print('{0:.3f} {1:.3f} {2:.3f}'.format(speed, ll, rl))
            if rl > 0.01 and rl < 10:
                r.rotate_left()
            elif ll > 0.01 and ll < 10:
                r.rotate_right()
            else:
                speed = 10.0*tanh(log(ll)+log(rl))
                if speed>5.0:
                    speed = 5.0
                r.move_forward(speed=speed)
            time.sleep(0.1)


def redis_control():
    with VRep.connect("127.0.0.1", 19999) as api:
        r = PioneerP3DX('Pioneer_p3dx', api)
        Red = Redis()
        pubsub = Red.pubsub()
        pubsub.subscribe('ROBOT')
        speed = 0.0
        print('listening..')
        prev = ''
        while True:
            msg = pubsub.get_message(ignore_subscribe_messages=True)
            if msg:
                cmd = msg['data'].decode('utf-8').split(',')[-1][:-1].strip()
                try:
                    invoke(r, cmd)
                except NotImplementedError:
                    print(cmd, 'not implemented!')
            # build percepts string
            rl = r.right_length()
            ll = r.left_length()
            percepts = '[sonar({0:.3f}, {1:0.3f})]'.format(ll, rl)
            if percepts!= prev:
                prev = percepts
                Red.publish('ROBOT:PERCEPTS', percepts)


# Handling messages from the TR program
class MessageThread(threading.Thread):
    def __init__(self, client, q):
        self.running = True
        self.client = client
        self.queue = q
        threading.Thread.__init__(self)
        self.daemon = True

    def run(self):
        while self.running:
            p2pmsg = self.client.get_term()[0]
            self.queue.put(p2pmsg)

    def stop(self):
        self.running = False


class Vrep_Pedro(object):

    # sensors:
    # sonar( Ld, Rd)
    #

    # motion:
    # move_forward(speed)
    # stop()
    # turnLeft(angleSpeed)
    # turnRight(angleSpeed)

    def __init__(self, vrep_client_id):
        self.vrep_client_id = vrep_client_id
        self.tr_client_addr = None
        self.client = PedroClient()
        # register vrep_pedro as the name of this process with Pedro
        self.client.register("vrep_pedro")
        self.queue = queue.Queue(0)
        self.message_thread = MessageThread(self.client, self.queue)
        self.message_thread.start()
        self.set_client("['127.0.0.1']")

    # def methods for sensing and acting to the robot in the simulator
    def move_forward(self, speed):
        print("move_forward", speed)
        self.vrep_client_id.move_forward(speed)

    def stop_move(self):
        print("stop")
        self.vrep_client_id.move_forward(0.0)

    def turn_left(self, angleSpeed):
        self.vrep_client_id.rotate_left(angleSpeed)

    def turn_right(self, angleSpeed):
        self.vrep_client_id.rotate_right(angleSpeed)

    def set_client(self, addr):
        self.tr_client_addr = addr

    def send_percept(self, percepts_string):
        print("send_percept", str(self.tr_client_addr), percepts_string)
        if self.client.p2p(self.tr_client_addr, percepts_string) == 0:
            print("Error", percepts_string)

    def exit(self):
        self.message_thread.stop()
        self.client.p2p("messages:" + self.tr_client_addr, "quiting")

    def process_initialize(self):
        # Block unitil message arrives
        p2pmsg = self.queue.get()
        print(p2pmsg)
        message = p2pmsg.args[2]
        if str(message) == 'initialise_':
            # get the sender address
            percepts_addr = p2pmsg.args[1]
            print("percepts_addr", str(percepts_addr))
            self.set_client(percepts_addr)
            # VREP code goes here so the visualization can
            # send back any initial percepts (iniital state)
            # create a string representing a list of initial percepts
            # say init_percepts and call
            # self.parent.send_percept(init_percepts)
            init_percepts = '[]'
            self.send_percept(init_percepts)
        else:
            print("Didn't get initialise_ message")

    def process_controls(self):
        while not self.queue.empty():
            p2pmsg = self.queue.get()
            msg = p2pmsg.args[2]
            print("process_controls message: ", str(msg))
            if not msg.is_pstruct() or msg.functor.val != 'controls':
                print("CONTROLS: ", str(msg))
                assert False
            actions = msg.args[0]
            if not actions.is_plist():
                print("CONTROLS: ", str(actions))
                assert False
            for a in actions.toList():
                self.process_action(a)

    def process_action(self, message):
        if not message.is_pstruct():
            return
        functor = message.functor
        if not functor.is_patom():
            return
        cmd_type = functor.val
        cmd = message.args[0]
        if not cmd.is_pstruct():
            return
        if cmd_type == 'stop_':
            if cmd.functor.val == 'move' and cmd.arity() == 2:
                self.stop_move()
        elif cmd_type in ['start_', 'mod_']:
            if cmd.functor.val == 'move' and cmd.arity() == 1:
                speed = cmd.args[0].val
                self.move_forward(speed)
            if cmd.functor.val == 'turn_left' and cmd.arity() == 1:
                speed = cmd.args[0].val
                self.turn_left(speed)
            if cmd.functor.val == 'turn_right' and cmd.arity() == 1:
                speed = cmd.args[0].val
                self.turn_right(speed)


def pedro_control():
    with VRep.connect("127.0.0.1", 19997) as api:
        myRobot = PioneerP3DX('Pioneer_p3dx', api)
        vrep_pedro = Vrep_Pedro(myRobot)
        # wait for and process initialize_ message
        vrep_pedro.process_initialize()

        previous_percept = None
        time.sleep(1)

        print('Connected to V-REP and PEDRO, listening...')

        while True:
            # build percepts string
            rl = myRobot.right_length()
            ll = myRobot.left_length()
            percept = '[sonar({0:.3f}, {1:0.3f})]'.format(ll, rl)
            if percept != previous_percept:
                previous_percept = percept
                vrep_pedro.send_percept(percept)
