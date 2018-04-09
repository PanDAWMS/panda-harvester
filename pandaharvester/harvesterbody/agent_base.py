import os
import threading
from pandaharvester.harvestercore import core_utils


# base class for agents
class AgentBase(threading.Thread):

    # constructor
    def __init__(self, single_mode):
        threading.Thread.__init__(self)
        self.singleMode = single_mode
        self.stopEvent = None
        self.os_pid = os.getpid()

    # set stop event
    def set_stop_event(self, stop_event):
        self.stopEvent = stop_event

    # check if going to be terminated
    def terminated(self, wait_interval, randomize=True):
        if self.singleMode:
            return True
        return core_utils.sleep(wait_interval, self.stopEvent, randomize)

    # get process identifier
    def get_pid(self):
        return '{0}-{1}'.format(self.os_pid, self.ident)

    # make logger
    def make_logger(self, base_log, token=None, method_name=None, send_dialog=True):
        if send_dialog and hasattr(self, 'dbProxy'):
            hook = self.dbProxy
        else:
            hook = None
        return core_utils.make_logger(base_log, token=token, method_name=method_name, hook=hook)
