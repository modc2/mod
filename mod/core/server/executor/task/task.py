
import os
import sys
import time
import queue
import weakref
import itertools
import threading
import asyncio
from loguru import logger
from typing import *
from concurrent.futures._base import Future
import time
from tqdm import tqdm
import traceback

def detailed_error(e:Exception) -> str:
    tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
    return {
        'error': str(e),
        'traceback': ''.join(tb_lines)
    }

class Task:
    def __init__(self, 
                fn:Union[str, callable],
                params:dict, 
                timeout:int=10, 
                path = None, 
                value:int=1,
                **extra_kwargs):
        
        self.fn = fn if callable(fn) else lambda *args, **kwargs: fn
        self.params = params or {}
        self.start_time = time.time() # the time the task was created
        self.timeout = timeout # the timeout of the task
        self.path = os.path.abspath(path) if path != None else None
        self.status = 'pending' # pending, running, done
        self.future = Future()
        self.value = value
        add_future_attributes = ['_condition', '_state', '_waiters', 'cancel', 'running', 'done', 'result']
        for attr in add_future_attributes:
            setattr(self, attr, getattr(self.future, attr)) 

    def run(self):
        """Run the given work item"""
        if (not self.future.set_running_or_notify_cancel()) or (time.time() - self.start_time) > self.timeout:
            self.future.set_exception(TimeoutError('Task timed out'))
        try:
            result = self.fn(**self.params)
            self.status = 'complete'
        except Exception as e:
            result = detailed_error(e)
            self.status = 'failed'
        self.future.set_result(result)

    def __lt__(self, other):
        return self.value < other.value

    @classmethod
    def null(cls):
        return (sys.maxsize, Task(None, {}))


