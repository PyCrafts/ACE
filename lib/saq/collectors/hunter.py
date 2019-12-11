# vim: sw=4:ts=4:et:cc=120
#
# ACE Hunting System
#

# How this works:
# A HunterCollector reads the config and loads all the sections that start with hunt_type_
# each of these configuration settings defines a "hunt type" (example: qradar, splunk, etc...)
# each section looks like this:
# [hunt_type_TYPE]
# module = path.to.module
# class = HuntClass
# rule_dirs = hunts/dir1,hunts/dir2
# concurrency_limit = LIMIT
# 
# TYPE is some unique string that identifies the type of the hunt
# the module and class settings define the class that will be used that extends saq.collector.hunter.Hunt
# rule_dirs contains a list of directories to load rules ini formatted rules from
# and concurrency_limit defines concurrency constraints (see below)
#
# Each of these "types" is managed by a HuntManager which loads the Hunt-based rules and manages the execution
# of these rules, apply any concurrency constraints required.
#

import configparser
import datetime
import importlib
import logging
import operator
import os, os.path
import signal
import threading
import sqlite3

import saq
from saq.collectors import Collector, Submission
from saq.constants import *
from saq.error import report_exception
from saq.network_semaphore import NetworkSemaphoreClient
from saq.util import local_time, create_timedelta, abs_path, create_directory

def get_hunt_db_path(hunt_type):
    return os.path.join(saq.DATA_DIR, saq.CONFIG['collection']['persistence_dir'], 'hunt', f'{hunt_type}.db')

def open_hunt_db(hunt_type):
    """Utility function to open sqlite3 database with correct parameters."""
    return sqlite3.connect(get_hunt_db_path(hunt_type), detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)

class Hunt(object):
    """Abstract class that represents a single hunt."""

    def __init__(self, enabled=None, name=None, description=None, type=None,
                       frequency=None, tags=[]):

        self.enabled = enabled
        self.name = name
        self.description = description
        self.type = type
        self.frequency = frequency
        self.tags = tags

        # the last time the hunt was executed
        # see the last_executed_time property
        #self.last_executed_time = None # datetime.datetime

        # a threading.RLock that is held while executing
        self.execution_lock = threading.RLock()

        # a way for the controlling thread to wait for the hunt execution thread to start
        self.startup_barrier = threading.Barrier(2)

    @property
    def last_executed_time(self):
        # if we don't already have this value then load it from the sqlite db
        if hasattr(self, '_last_executed_time'):
            return self._last_executed_time
        else:
            with open_hunt_db(self.type) as db:
                c = db.cursor()
                c.execute("SELECT last_executed_time FROM hunt WHERE hunt_name = ?",
                         (self.name,))
                row = c.fetchone()
                if row is None:
                    self._last_executed_time = None
                    return self._last_executed_time
                else:
                    self._last_executed_time = row[0]
                    return self._last_executed_time

    @last_executed_time.setter
    def last_executed_time(self, value):
        with open_hunt_db(self.type) as db:
            c = db.cursor()
            c.execute("UPDATE hunt SET last_executed_time = ? WHERE hunt_name = ?",
                     (value, self.name))
            db.commit()

        self._last_executed_time = value

    def __str__(self):
        return f"Hunt({self.name}[{self.type}])"

    def cancel(self):
        """Called when the hunt needs to be cancelled, such as when the system is shutting down.
           This must be safe to call even if the hunt is not currently executing."""
        raise NotImplementedError()

    def execute_with_lock(self):
        # we use this lock to determine if a hunt is running, and, to wait for execution to complete.
        logging.debug(f"waiting for execution lock on {self}")
        self.execution_lock.acquire()

        # remember the last time we executed
        self.last_executed_time = datetime.datetime.now()

        # notify the manager that this is now executing
        # this releases the manager thread to continue processing hunts
        logging.debug(f"clearing barrier for {self}")
        self.startup_barrier.wait()

        submission_list = None

        try:
            logging.info(f"executing {self}")
            start_time = datetime.datetime.now()
            return self.execute()
            self.record_execution_time(datetime.datetime.now() - start_time)
        except Exception as e:
            logging.error(f"{self} failed: {e}")
            report_exception()
            self.record_hunt_exception(e)
        finally:
            self.startup_barrier.reset()
            self.execution_lock.release()

    def execute(self):
        """Called to execute the hunt. Returns a list of zero or more saq.collector.Submission objects."""
        raise NotImplementedError()

    def wait(self, *args, **kwargs):
        """Waits for the hunt to complete execution. If the hunt is not running then it returns right away.
           Returns False if a timeout is set and the lock is not released during that timeout.
           Additional parameters are passed to execution_lock.acquire()."""
        result = self.execution_lock.acquire(*args, **kwargs)
        if result:
            self.execution_lock.release()

        return result

    @property
    def running(self):
        """Returns True if the hunt is currently executing, False otherwise."""
        # when the hunt is executing it will have this lock enabled
        result = self.execution_lock.acquire(blocking=False)
        if result:
            self.execution_lock.release()
            return False

        return True

    def load_from_ini(self, path):
        """Loads the settings for the hunt from an ini formatted file. This function must return the 
           ConfigParser object used to load the settings."""
        config = configparser.ConfigParser()
        config.read(path)

        section_rule = config['rule']

        self.enabled = section_rule.getboolean('enabled')
        self.name = section_rule['name']
        self.description = section_rule['description']
        self.type = section_rule['type']
        self.frequency = create_timedelta(section_rule['frequency'])
        self.tags = [_.strip() for _ in section_rule['tags'].split(',')]

        return config

    @property
    def ready(self):
        """Returns True if the hunt is ready to execute, False otherwise."""
        # if it's already running then it's not ready to run again
        if self.running:
            return False

        # if we haven't executed it yet then it's ready to go
        if self.last_executed_time is None:
            return True

        # otherwise we're not ready until it's past the next execution time
        return datetime.datetime.now() >= self.next_execution_time

    @property
    def next_execution_time(self):
        """Returns the next time this hunt should execute."""
        # if it hasn't executed at all yet, then execute it now
        if self.last_executed_time is None:
            return datetime.datetime.now() 

        return self.last_executed_time + self.frequency

    def record_execution_time(self, time_delta):
        """Record the amount of time it took to execute this hunt."""
        pass

    def record_hunt_exception(self, exception):
        """Record the details of a failed hunt."""
        pass

CONCURRENCY_TYPE_NETWORK_SEMAPHORE = 'network_semaphore'
CONCURRENCY_TYPE_LOCAL_SEMAPHORE = 'local_semaphore'

class HuntManager(object):
    """Manages the hunting for a single hunt type."""
    def __init__(self, collector, hunt_type, rule_dirs, hunt_cls, concurrency_limit, persistence_dir):
        assert isinstance(collector, Collector)
        assert isinstance(hunt_type, str)
        assert isinstance(rule_dirs, list)
        assert issubclass(hunt_cls, Hunt)
        assert concurrency_limit is None or isinstance(concurrency_limit, int) or isinstance(concurrency_limit, str)
        assert isinstance(persistence_dir, str)

        # reference to the collector (used to send the Submission objects)
        self.collector = collector

        # primary execution thread
        self.manager_thread = None

        # shutdown valve
        self.manager_control_event = threading.Event()
        self.wait_control_event = threading.Event()

        # control signal to reload the hunts (set by SIGHUP indirectly)
        self.reload_hunts_flag = False

        # the type of hunting this manager manages
        self.hunt_type = hunt_type

        # the list of directories that contain the hunt configuration ini files for this type of hunt
        self.rule_dirs = rule_dirs

        # the class used to instantiate the rules in the given rules directories
        self.hunt_cls = hunt_cls

        # sqlite3 database used to keep track of hunt persistence data
        create_directory(os.path.dirname(get_hunt_db_path(self.hunt_type)))
        if not os.path.exists(get_hunt_db_path(self.hunt_type)):
            with open_hunt_db(self.hunt_type) as db:
                c = db.cursor()
                # XXX have to support all future schemas here -- not a great design
                c.execute("""
CREATE TABLE hunt ( 
    hunt_name TEXT NOT NULL,
    last_executed_time timestamp,
    last_end_time timestamp )""")
                c.execute("""
CREATE UNIQUE INDEX idx_name ON hunt(hunt_name)""")
                db.commit()

        # the list of Hunt objects that are being managed
        self._hunts = []

        # the type of concurrency contraint this type of hunt uses (can be None)
        # use the set_concurrency_limit() function to change it
        self.concurrency_type = None

        # the local threading.Semaphore if the type is CONCURRENCY_TYPE_LOCAL_SEMAPHORE
        # or the string name of the network semaphore if tye type is CONCURRENCY_TYPE_NETWORK_SEMAPHORE
        self.concurrency_semaphore = None

        if concurrency_limit is not None:
            self.set_concurrency_limit(concurrency_limit)

        # this is set to True if load_hunts_from_config() is called
        # and used when reload_hunts_flag is set
        self.hunts_loaded_from_config = False

    def __str__(self):
        return f"Hunt Manager({self.hunt_type})"

    @property
    def hunts(self):
        """Returns a sorted copy of the list of hunts in execution order."""
        return sorted(self._hunts, key=operator.attrgetter('next_execution_time'))

    def signal_reload(self):
        """Signals to this manager that the hunts should be reloaded.
           The work takes place on the manager thread."""
        logging.debug("received signal to reload hunts")
        self.reload_hunts_flag = True
        self.wait_control_event.set()

    def reload_hunts(self):
        """Reloads the hunts. This is called when reload_hunts_flag is set to True.
           If the hunts were loaded from the configuration then the current Hunt objects
           are discarded and new ones are loaded from configuration.
           Otherwise this function does nothing."""

        self.reload_hunts_flag = False
        if not self.hunts_loaded_from_config:
            logging.debug(f"{self} received signal to reload but hunts were not loaded from configuration")
            return

        logging.info(f"{self} reloading hunts")

        # first cancel any currently executing hunts
        self.cancel_hunts()
        
        # then release all the hunts and load the new ones
        self._hunts = []
        self.load_hunts_from_config()

    def start(self):
        self.manager_control_event.clear()
        self.load_hunts_from_config()
        self.manager_thread = threading.Thread(target=self.loop, name=f"Hunt Manager {self.hunt_type}")
        self.manager_thread.start()

    def debug(self):
        self.manager_control_event.clear()
        self.load_hunts_from_config()
        self.execute()

    def stop(self):
        logging.info(f"stopping {self}")
        self.manager_control_event.set()
        self.wait_control_event.set()

    def wait(self, *args, **kwargs):
        self.manager_control_event.wait(*args, **kwargs)
        for hunt in self._hunts:
            hunt.wait(*args, **kwargs)

    def loop(self):
        logging.debug(f"started {self}")
        while not self.manager_control_event.is_set():
            try:
                self.execute()
            except Exception as e:
                logging.error(f"uncaught exception {e}")
                report_exception()
                self.manager_control_event.wait(timeout=1)

            if self.reload_hunts_flag:
                self.reload_hunts()

        logging.debug(f"stopped {self}")

    def execute(self):
        # the next one to run should be the first in our list
        for hunt in self.hunts:
            if hunt.ready:
                self.execute_hunt(hunt)
                continue
            else:
                # this one isn't ready so wait for this hunt to be ready
                wait_time = (hunt.next_execution_time - datetime.datetime.now()).total_seconds()
                logging.info(f"next hunt is {hunt} @ {hunt.next_execution_time} ({wait_time} seconds)")
                self.wait_control_event.wait(wait_time)
                self.wait_control_event.clear()

                # if a hunt ends while we're waiting, wait_control_event will break out before wait_time seconds
                # at this point, it's possible there's another hunt ready to execute before this one we're waiting on
                # so no matter what, we break out so that we re-enter with a re-ordered list of hunts
                return

    def execute_hunt(self, hunt):
        # are we ready to run another one of these types of hunts?
        # NOTE this will BLOCK until a semaphore is ready OR this manager is shutting down
        start_time = datetime.datetime.now()
        hunt.semaphore = self.acquire_concurrency_lock()

        if self.manager_control_event.is_set():
            if hunt.semaphore is not None:
                hunt.semaphore.release()
            return

        # keep track of how long it's taking to acquire the resource
        if hunt.semaphore is not None:
            self.record_semaphore_acquire_time(datetime.datetime.now() - start_time)

        # start the execution of the hunt on a new thread
        hunt_execution_thread = threading.Thread(target=self.execute_threaded_hunt, 
                                                 args=(hunt,),
                                                 name=f"Hunt Execution {hunt}")
        hunt_execution_thread.start()

        # wait for the signal that the hunt has started
        # this will block for a short time to ensure we don't wrap back around before the 
        # execution lock is acquired
        hunt.startup_barrier.wait()

    def execute_threaded_hunt(self, hunt):
        try:
            submissions = hunt.execute_with_lock()
        except Exception as e:
            logging.error(f"uncaught exception: {e}")
            report_exception()
        finally:
            self.release_concurrency_lock(hunt.semaphore)
            # at this point this hunt has finished and is eligible to execute again
            self.wait_control_event.set()

        for submission in submissions:
            self.collector.submission_list.put(submission)

    def cancel_hunts(self):
        """Cancels all the currently executing hunts."""
        for hunt in self._hunts: # order doesn't matter here
            try:
                if hunt.running:
                    logging.info("cancelling {hunt}")
                    hunt.cancel()
                    hunt.wait()
            except Exception as e:
                logging.info("unable to cancel {hunt}: {e}")

    def set_concurrency_limit(self, limit):
        """Sets the concurrency limit for this type of hunt.
           If limit is a string then it's considered to be the name of a network semaphore.
           If limit is an integer then a local threading.Semaphore is used."""
        try:
            # if the limit value is an integer then it's a local semaphore
            self.concurrency_type = CONCURRENCY_TYPE_LOCAL_SEMAPHORE
            self.concurrency_semaphore = threading.Semaphore(int(limit))
            logging.debug(f"concurrency limit for {self.hunt_type} set to local limit {limit}")
        except ValueError:
            # otherwise it's the name of a network semaphore
            self.concurrency_type = CONCURRENCY_TYPE_NETWORK_SEMAPHORE
            self.concurrency_semaphore = limit
            logging.debug(f"concurrency limit for {self.hunt_type} set to "
                          f"network semaphore {self.concurrency_semaphore}")

    def acquire_concurrency_lock(self):
        """Acquires a concurrency lock for this type of hunt if specified in the configuration for the hunt.
           Returns a NetworkSemaphoreClient object if the concurrency_type is CONCURRENCY_TYPE_NETWORK_SEMAPHORE
           or a reference to the threading.Semaphore object if concurrency_type is CONCURRENCY_TYPE_LOCAL_SEMAPHORE.
           Immediately returns None if non concurrency limits are in place for this type of hunt."""

        if self.concurrency_type is None:
            return None

        result = None
        start_time = datetime.datetime.now()
        if self.concurrency_type == CONCURRENCY_TYPE_NETWORK_SEMAPHORE:
            logging.debug(f"acquiring network concurrency semaphore {self.concurrency_semaphore} "
                          f"for hunt type {self.hunt_type}")
            result = NetworkSemaphoreClient(cancel_request_callback=self.manager_control_event.is_set)
                                                                # make sure we cancel outstanding request 
                                                                # when shutting down
            result.acquire(self.concurrency_semaphore)
        else:
            logging.debug(f"acquiring local concurrency semaphore for hunt type {self.hunt_type}")
            while not self.manager_control_event.is_set():
                if self.concurrency_semaphore.acquire(blocking=True, timeout=0.1):
                    result = self.concurrency_semaphore
                    break

        if result is not None:
            total_seconds = (datetime.datetime.now() - start_time).total_seconds()
            logging.debug(f"acquired concurrency semaphore for hunt type {self.hunt_type} in {total_seconds} seconds")

        return result

    def release_concurrency_lock(self, semaphore):
        if semaphore is not None:
            # both types of semaphores support this function call
            logging.debug(f"releasing concurrency semaphore for hunt type {self.hunt_type}")
            semaphore.release()

    def load_hunts_from_config(self):
        """Loads the hunts from the configuration settings.
           Returns True if all of the hunts were loaded correctly, False if any errors occurred."""
        for rule_dir in self.rule_dirs:
            rule_dir = abs_path(rule_dir)
            if not os.path.isdir(rule_dir):
                logging.error(f"rules directory {rule_dir} specified for {self} is not a directory")
                continue

            # load each .ini file found in this rules directory
            for root, dirnames, filenames in os.walk(rule_dir):
                for hunt_config in filenames:
                    if not hunt_config.endswith('.ini'):
                        continue

                    hunt_config = os.path.join(root, hunt_config)
                    hunt = self.hunt_cls()
                    logging.debug(f"loading hunt from {hunt_config}")
                    hunt.load_from_ini(hunt_config)
                    hunt.type = self.hunt_type
                    logging.info(f"loaded {hunt} from {hunt_config}")
                    self.add_hunt(hunt)

        # remember that we loaded the hunts from the configuration file
        # this is used when we receive the signal to reload the hunts
        self.hunts_loaded_from_config = True

    def add_hunt(self, hunt):
        assert isinstance(hunt, Hunt)
        if hunt.type != self.hunt_type:
            raise ValueError(f"hunt {hunt} has wrong type for {self}")

        # make sure this hunt doesn't already exist
        for _hunt in self._hunts:
            if _hunt.name == hunt.name:
                raise KeyError(f"duplicate hunt {hunt.name}")

        with open_hunt_db(self.hunt_type) as db:
            c = db.cursor()
            c.execute("INSERT OR IGNORE INTO hunt(hunt_name) VALUES ( ? )",
                     (hunt.name,))
            db.commit()

        self._hunts.append(hunt)
        self.wait_control_event.set()
        return hunt

    def remove_hunt(self, hunt):
        assert isinstance(hunt, Hunt)

        with open_hunt_db(hunt.type) as db:
            c = db.cursor()
            c.execute("DELETE FROM hunt WHERE hunt_name = ?",
                     (hunt.name,))
            db.commit()

        self._hunts.remove(hunt)
        self.wait_control_event.set()
        return hunt

    #def update_hunt(self, hunt):
        #assert isinstance(hunt, Hunt)
        #if hunt.type != self.hunt_type:
            #raise ValueError(f"hunt {hunt} has wrong type for {self}")

        #for index, hunt in enumerate(self.hunts):
            #old_hunt = self.hunts[index]
            #self.hunts[index] = hunt
            #self.wait_control_event.set()
            #return old_hunt

        #raise KeyError(f"unknown hunt {hunt.name}")

    def record_semaphore_acquire_time(self, time_delta):
        pass

class HunterCollector(Collector):
    """Manages and executes the hunts configured for the system."""
    def __init__(self, *args, **kwargs):
        super().__init__(service_config=saq.CONFIG['service_hunter'],
                         workload_type='hunter', 
                         delete_files=True, 
                         *args, **kwargs)

        # each type of hunt is grouped and managed as a single unit
        self.hunt_managers = [] # of HuntManager object

    def reload_service(self, *args, **kwargs):
        # pass along the SIGHUP to the hunt managers
        super().reload_service(*args, **kwargs)
        for manager in self.hunt_managers:
            manager.signal_reload()

    def debug_extended_collection(self):
        self.extended_collection()

    def extended_collection(self):
        # load each type of hunt from the configuration settings
        logging.info("starting hunt managers...")
        for section_name in saq.CONFIG.sections():
            if not section_name.startswith('hunt_type_'):
                continue

            section = saq.CONFIG[section_name]

            if 'rule_dirs' not in section:
                logging.error(f"config section {section} does not define rule_dirs")
                continue

            hunt_type = section_name[len('hunt_type_'):]

            # make sure the class definition for this hunt is valid
            module_name = section['module']
            try:
                _module = importlib.import_module(module_name)
            except Exception as e:
                logging.error(f"unable to import hunt module {module_name}: {e}")
                continue

            class_name = section['class']
            try:
                class_definition = getattr(_module, class_name)
            except AttributeError as e:
                logging.error("class {} does not exist in module {} in hunt {} config".format(
                              class_name, module_name, section))
                continue

            logging.debug(f"loading hunt manager for {hunt_type} class {class_definition}")
            manager = HuntManager(collector=self,
                                  hunt_type=hunt_type, 
                                  rule_dirs=[_.strip() for _ in section['rule_dirs'].split(',')],
                                  hunt_cls=class_definition,
                                  concurrency_limit=section.get('concurrency_limit', fallback=None),
                                  persistence_dir=self.persistence_dir)

            if self.service_is_debug:
                manager.debug()
            else:
                manager.start()

            self.hunt_managers.append(manager)

        if self.service_is_debug:
            return

        # wait for this service the end
        self.service_shutdown_event.wait()

        # then stop the managers and wait for them to complete
        for manager in self.hunt_managers:
            manager.stop()

        # then wait for the managers to complete
        for manager in self.hunt_managers:
            manager.wait()
