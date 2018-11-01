#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2014 - 2018 Adam.Dybbroe

# Author(s):

#   Adam.Dybbroe <adam.dybbroe@smhi.se>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Posttroll runner for PPS
"""
import os
import ConfigParser
import sys
import socket
from glob import glob
import stat
from urlparse import urlparse
import posttroll.subscriber
from posttroll.publisher import Publish
from posttroll.message import Message

from subprocess import Popen, PIPE
import threading
import Queue
from datetime import datetime, timedelta
from trollsift.parser import parse

import logging
LOG = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get('PPSRUNNER_CONFIG_DIR', './')
PPS_SCRIPT = os.environ['PPS_SCRIPT']

LOG.debug("PPS_SCRIPT = " + str(PPS_SCRIPT))

CONF = ConfigParser.ConfigParser()
CONF.read(os.path.join(CONFIG_PATH, "pps_config.cfg"))

MODE = os.getenv("SMHI_MODE")
if MODE is None:
    MODE = "offline"


OPTIONS = {}
for option, value in CONF.items(MODE, raw=True):
    OPTIONS[option] = value

# PPS_OUTPUT_DIR = os.environ.get('SM_PRODUCT_DIR', OPTIONS['pps_outdir'])
PPS_OUTPUT_DIR = OPTIONS['pps_outdir']
STATISTICS_DIR = OPTIONS.get('pps_statistics_dir')

LVL1_NPP_PATH = os.environ.get('LVL1_NPP_PATH', None)
LVL1_EOS_PATH = os.environ.get('LVL1_EOS_PATH', None)


servername = None
servername = socket.gethostname()
SERVERNAME = OPTIONS.get('servername', servername)

NWP_FLENS = [3, 6, 9, 12, 15, 18, 21, 24]


SUPPORTED_NOAA_SATELLITES = ['NOAA-15', 'NOAA-18', 'NOAA-19']
SUPPORTED_METOP_SATELLITES = ['Metop-B', 'Metop-A']
SUPPORTED_EOS_SATELLITES = ['EOS-Terra', 'EOS-Aqua']
SUPPORTED_JPSS_SATELLITES = ['Suomi-NPP', 'NOAA-20', 'NOAA-21']

SUPPORTED_PPS_SATELLITES = (SUPPORTED_NOAA_SATELLITES +
                            SUPPORTED_METOP_SATELLITES +
                            SUPPORTED_EOS_SATELLITES +
                            SUPPORTED_JPSS_SATELLITES)

GEOLOC_PREFIX = {'EOS-Aqua': 'MYD03', 'EOS-Terra': 'MOD03'}
DATA1KM_PREFIX = {'EOS-Aqua': 'MYD021km', 'EOS-Terra': 'MOD021km'}

PPS_SENSORS = ['amsu-a', 'amsu-b', 'mhs', 'avhrr/3', 'viirs', 'modis']
REQUIRED_MW_SENSORS = {}
REQUIRED_MW_SENSORS['NOAA-15'] = ['amsu-a', 'amsu-b']
REQUIRED_MW_SENSORS['NOAA-18'] = ['amsu-a', 'mhs']
REQUIRED_MW_SENSORS['NOAA-19'] = ['amsu-a', 'mhs']
REQUIRED_MW_SENSORS['Metop-A'] = ['amsu-a', 'mhs']
REQUIRED_MW_SENSORS['Metop-B'] = ['amsu-a', 'mhs']
NOAA_METOP_PPS_SENSORNAMES = ['avhrr/3', 'amsu-a', 'amsu-b', 'mhs']

METOP_NAME_LETTER = {'metop01': 'metopb', 'metop02': 'metopa'}
METOP_NAME = {'metop01': 'Metop-B', 'metop02': 'Metop-A'}
METOP_NAME_INV = {'metopb': 'metop01', 'metopa': 'metop02'}

SATELLITE_NAME = {'NOAA-19': 'noaa19', 'NOAA-18': 'noaa18',
                  'NOAA-15': 'noaa15',
                  'Metop-A': 'metop02', 'Metop-B': 'metop01',
                  'Metop-C': 'metop03',
                  'Suomi-NPP': 'npp',
                  'NOAA-20': 'noaa20', 'NOAA-21': 'noaa21',
                  'EOS-Aqua': 'eos2', 'EOS-Terra': 'eos1'}
SENSOR_LIST = {}
for sat in SATELLITE_NAME:
    if sat in ['NOAA-15']:
        SENSOR_LIST[sat] = ['avhrr/3', 'amsu-b', 'amsu-a']
    elif sat in ['EOS-Aqua', 'EOS-Terra']:
        SENSOR_LIST[sat] = 'modis'
    elif sat in ['Suomi-NPP', 'NOAA-20', 'NOAA-21']:
        SENSOR_LIST[sat] = 'viirs'
    else:
        SENSOR_LIST[sat] = ['avhrr/3', 'mhs', 'amsu-a']


METOP_SENSOR = {'amsu-a': 'amsua', 'avhrr/3': 'avhrr',
                'amsu-b': 'amsub', 'hirs/4': 'hirs'}
METOP_NUMBER = {'b': '01', 'a': '02'}

PPS_OUT_PATTERN = "S_NWC_{segment}_{orig_platform_name}_{orbit_number:05d}_{start_time:%Y%m%dT%H%M%S%f}Z_{end_time:%Y%m%dT%H%M%S%f}Z.{extention}"
PPS_OUT_PATTERN_MULTIPLE = "S_NWC_{segment1}_{segment2}_{orig_platform_name}_{orbit_number:05d}_{start_time:%Y%m%dT%H%M%S%f}Z_{end_time:%Y%m%dT%H%M%S%f}Z.{extention}"
PPS_STAT_PATTERN = "S_NWC_{segment}_{orig_platform_name}_{orbit_number:05d}_{start_time:%Y%m%dT%H%M%S%f}Z_{end_time:%Y%m%dT%H%M%S%f}Z_statistics.xml"

#: Default time format
_DEFAULT_TIME_FORMAT = '%Y-%m-%d %H:%M:%S'

#: Default log format
_DEFAULT_LOG_FORMAT = '[%(levelname)s: %(asctime)s : %(name)s] %(message)s'

_PPS_LOG_FILE = os.environ.get('PPSRUNNER_LOG_FILE', None)
_PPS_LOG_FILE = OPTIONS.get('pps_log_file', _PPS_LOG_FILE)


LOG.debug("PYTHONPATH: " + str(sys.path))
from nwcsafpps_runner.prepare_nwp import update_nwp
SATNAME = {'Aqua': 'EOS-Aqua'}


class SceneId(object):

    def __init__(self, platform_name, orbit_number, starttime, threshold=5):
        self.platform_name = platform_name
        self.orbit_number = orbit_number
        self.starttime = starttime
        self.threshold = threshold

    def __str__(self):

        return (str(self.platform_name) + '_' +
                str(self.orbit_number) + '_' +
                str(self.starttime.strftime('%Y%m%d%H%M')))

    def __eq__(self, other):

        return (self.platform_name == other.platform_name and
                self.orbit_number == other.orbit_number and
                abs(self.starttime - other.starttime) < timedelta(minutes=self.threshold))


def message_uid(msg):
    """Create a unique id/key-name for the scene."""

    orbit_number = int(msg.data['orbit_number'])
    platform_name = msg.data['platform_name']
    starttime = msg.data['start_time']

    return SceneId(platform_name, orbit_number, starttime)


class ThreadPool(object):

    def __init__(self, max_nthreads=None):

        self.jobs = set()
        self.sema = threading.Semaphore(max_nthreads)
        self.lock = threading.Lock()

    def new_thread(self, job_id, group=None, target=None, name=None, args=(), kwargs={}):

        def new_target(*args, **kwargs):
            with self.sema:
                result = target(*args, **kwargs)

            self.jobs.remove(job_id)
            return result

        with self.lock:
            if job_id in self.jobs:
                LOG.info("Job with id %s already running!", str(job_id))
                return

            self.jobs.add(job_id)

        thread = threading.Thread(group, new_target, name, args, kwargs)
        thread.start()


class PpsRunError(Exception):
    pass


def logreader(stream, log_func):
    while True:
        s = stream.readline()
        if not s:
            break
        log_func(s.strip())
    stream.close()


def get_outputfiles(path, platform_name, orb):
    """From the directory path and satellite id and orbit number scan the directory
    and find all pps output files matching that scene and return the full
    filenames. Since the orbit number is unstable there might be more than one
    scene with the same orbit number and platform name. In order to avoid
    picking up an older scene we check the file modifcation time, and if the
    file is too old we discard it!

    """

    h5_output = (os.path.join(path, 'S_NWC') + '*' +
                 str(METOP_NAME_LETTER.get(platform_name, platform_name)) +
                 '_' + '%.5d' % int(orb) + '_*.h5')
    LOG.info(
        "Match string to do a file globbing on hdf5 output files: " + str(h5_output))
    nc_output = (os.path.join(path, 'S_NWC') + '*' +
                 str(METOP_NAME_LETTER.get(platform_name, platform_name)) +
                 '_' + '%.5d' % int(orb) + '_*.nc')
    LOG.info(
        "Match string to do a file globbing on netcdf output files: " + str(nc_output))
    xml_output = (os.path.join(path, 'S_NWC') + '*' +
                  str(METOP_NAME_LETTER.get(platform_name, platform_name)) +
                  '_' + '%.5d' % int(orb) + '_*.xml')
    LOG.info(
        "Match string to do a file globbing on xml output files: " + str(xml_output))
    filelist = glob(h5_output) + glob(nc_output) + glob(xml_output)
    now = datetime.utcnow()
    time_threshold = timedelta(minutes=90.)
    filtered_flist = []
    for fname in filelist:
        mtime = datetime.utcfromtimestamp(os.stat(fname)[stat.ST_MTIME])
        if (now - mtime) < time_threshold:
            filtered_flist.append(fname)
        else:
            LOG.info("Found old PPS result: %s", fname)

    return filtered_flist


def terminate_process(popen_obj, scene):
    """Terminate a Popen process"""
    if popen_obj.returncode == None:
        popen_obj.kill()
        LOG.info(
            "Process timed out and pre-maturely terminated. Scene: " + str(scene))
    else:
        LOG.info(
            "Process finished before time out - workerScene: " + str(scene))
    return


def pps_worker(scene, publish_q, input_msg):
    """Start PPS on a scene

        scene = {'platform_name': platform_name,
                 'orbit_number': orbit_number,
                 'satday': satday, 'sathour': sathour,
                 'starttime': starttime, 'endtime': endtime}
    """

    try:
        LOG.debug("Starting pps runner for scene %s", str(scene))
        job_start_time = datetime.utcnow()

        if scene['platform_name'] in SUPPORTED_EOS_SATELLITES:
            cmdstr = "%s %s %s %s %s" % (PPS_SCRIPT,
                                         SATELLITE_NAME[
                                             scene['platform_name']],
                                         scene['orbit_number'], scene[
                                             'satday'],
                                         scene['sathour'])
        else:
            cmdstr = "%s %s %s 0 0" % (PPS_SCRIPT,
                                       SATELLITE_NAME[
                                           scene['platform_name']],
                                       scene['orbit_number'])

        if scene['platform_name'] in SUPPORTED_JPSS_SATELLITES and LVL1_NPP_PATH:
            cmdstr = cmdstr + ' ' + str(LVL1_NPP_PATH)
        elif scene['platform_name'] in SUPPORTED_EOS_SATELLITES and LVL1_EOS_PATH:
            cmdstr = cmdstr + ' ' + str(LVL1_EOS_PATH)

        import shlex
        myargs = shlex.split(str(cmdstr))
        LOG.info("Command " + str(myargs))
        my_env = os.environ.copy()
        # for envkey in my_env:
        # LOG.debug("ENV: " + str(envkey) + " " + str(my_env[envkey]))
        LOG.debug("PPS_OUTPUT_DIR = " + str(PPS_OUTPUT_DIR))
        LOG.debug(
            "...from config file = " + str(OPTIONS['pps_outdir']))
        if not os.path.isfile(PPS_SCRIPT):
            raise IOError("PPS script" + PPS_SCRIPT + " is not there!")
        elif not os.access(PPS_SCRIPT, os.X_OK):
            raise IOError(
                "PPS script" + PPS_SCRIPT + " cannot be executed!")

        try:
            pps_proc = Popen(
                myargs, shell=False, stderr=PIPE, stdout=PIPE)
        except PpsRunError:
            LOG.exception("Failed in PPS...")

        t__ = threading.Timer(
            20 * 60.0, terminate_process, args=(pps_proc, scene, ))
        t__.start()

        out_reader = threading.Thread(
            target=logreader, args=(pps_proc.stdout, LOG.info))
        err_reader = threading.Thread(
            target=logreader, args=(pps_proc.stderr, LOG.info))
        out_reader.start()
        err_reader.start()
        out_reader.join()
        err_reader.join()

        LOG.info(
            "Ready with PPS level-2 processing on scene: " + str(scene))

        # Now try perform som time statistics editing with ppsTimeControl.py from
        # pps:
        do_time_control = True
        try:
            from pps_time_control import PPSTimeControl
        except ImportError:
            LOG.warning("Failed to import the PPSTimeControl from pps")
            do_time_control = False

        if STATISTICS_DIR:
            pps_control_path = STATISTICS_DIR
        else:
            pps_control_path = my_env.get('STATISTICS_DIR')

        if do_time_control:
            LOG.info("Read time control ascii file and generate XML")
            platform_id = SATELLITE_NAME.get(
                scene['platform_name'], scene['platform_name'])
            LOG.info("pps platform_id = " + str(platform_id))
            txt_time_file = (os.path.join(pps_control_path, 'S_NWC_timectrl_') +
                             str(METOP_NAME_LETTER.get(platform_id, platform_id)) +
                             '_' + str(scene['orbit_number']) + '*.txt')
            LOG.info("glob string = " + str(txt_time_file))
            infiles = glob(txt_time_file)
            LOG.info(
                "Time control ascii file candidates: " + str(infiles))
            if len(infiles) == 1:
                infile = infiles[0]
                LOG.info("Time control ascii file: " + str(infile))
                ppstime_con = PPSTimeControl(infile)
                ppstime_con.sum_up_processing_times()
                ppstime_con.write_xml()

        # Now check what netCDF/hdf5 output was produced and publish
        # them:
        pps_path = my_env.get('SM_PRODUCT_DIR', PPS_OUTPUT_DIR)
        result_files = get_outputfiles(
            pps_path, SATELLITE_NAME[scene['platform_name']], scene['orbit_number'])
        LOG.info("PPS Output files: " + str(result_files))
        xml_files = get_outputfiles(
            pps_control_path, SATELLITE_NAME[scene['platform_name']], scene['orbit_number'])
        LOG.info("PPS summary statistics files: " + str(xml_files))

        # Now publish:
        for result_file in result_files + xml_files:
            # Get true start and end time from filenames and adjust the end time in
            # the publish message:
            filename = os.path.basename(result_file)
            LOG.info("file to publish = " + str(filename))
            try:
                try:
                    metadata = parse(PPS_OUT_PATTERN, filename)
                except ValueError:
                    metadata = parse(PPS_OUT_PATTERN_MULTIPLE, filename)
                    metadata['segment'] = '_'.join([metadata['segment1'],
                                                    metadata['segment2']])
                    del metadata['segment1'], metadata['segment2']
            except ValueError:
                metadata = parse(PPS_STAT_PATTERN, filename)

            endtime = metadata['end_time']
            starttime = metadata['start_time']

            to_send = input_msg.data.copy()
            to_send.pop('dataset', None)
            to_send.pop('collection', None)
            to_send['uri'] = (
                'ssh://%s/%s' % (SERVERNAME, result_file))
            to_send['uid'] = filename
            to_send['sensor'] = scene.get('instrument', None)
            if not to_send['sensor']:
                to_send['sensor'] = scene.get('sensor', None)

            to_send['platform_name'] = scene['platform_name']
            to_send['orbit_number'] = scene['orbit_number']
            if result_file.endswith("xml"):
                to_send['format'] = 'PPS-XML'
                to_send['type'] = 'XML'
            if result_file.endswith("nc"):
                to_send['format'] = 'CF'
                to_send['type'] = 'netCDF4'
            if result_file.endswith("h5"):
                to_send['format'] = 'PPS'
                to_send['type'] = 'HDF5'
            to_send['data_processing_level'] = '2'

            environment = MODE
            to_send['start_time'], to_send['end_time'] = starttime, endtime
            pubmsg = Message('/' + to_send['format'] + '/' +
                             to_send['data_processing_level'] +
                             '/norrköping/' + environment +
                             '/polar/direct_readout/',
                             "file", to_send).encode()
            LOG.debug("sending: " + str(pubmsg))
            LOG.info("Sending: " + str(pubmsg))
            publish_q.put(pubmsg)

            dt_ = datetime.utcnow() - job_start_time
            LOG.info("PPS on scene " + str(scene) +
                     " finished. It took: " + str(dt_))

        t__.cancel()

    except:
        LOG.exception('Failed in pps_worker...')
        raise


def ready2run(msg, files4pps):
    """Check wether pps is ready to run or not"""
    # """Start the PPS processing on a NOAA/Metop/S-NPP/EOS scene"""
    # LOG.debug("Received message: " + str(msg))

    from trollduction.producer import check_uri
    from socket import gethostbyaddr, gaierror

    LOG.debug("Ready to run...")
    LOG.info("Got message: " + str(msg))

    uris = []
    if (msg.type == 'dataset' and
            msg.data['platform_name'] in SUPPORTED_EOS_SATELLITES):
        LOG.info('Dataset: ' + str(msg.data['dataset']))
        LOG.info('Got a dataset on an EOS satellite')
        LOG.info('\t ...thus we can assume we have everything we need for PPS')
        for obj in msg.data['dataset']:
            uris.append(obj['uri'])
    elif msg.type == 'collection':
        if 'dataset' in msg.data['collection'][0]:
            for dataset in msg.data['collection']:
                uris.extend([mda['uri'] for mda in dataset['dataset']])

    elif msg.type == 'file':
        uris = [(msg.data['uri'])]
    else:
        LOG.debug(
            "Ignoring this type of message data: type = " + str(msg.type))
        return False

    try:
        level1_files = check_uri(uris)
    except IOError:
        LOG.info('One or more files not present on this host!')
        return False

    LOG.info("Sat and Sensor: " + str(msg.data['platform_name'])
             + " " + str(msg.data['sensor']))
    if msg.data['sensor'] not in PPS_SENSORS:
        LOG.info("Data from sensor " + str(msg.data['sensor']) +
                 " not needed by PPS " +
                 "Continue...")
        return False

    if msg.data['platform_name'] in SUPPORTED_EOS_SATELLITES:
        if msg.data['sensor'] not in ['modis', ]:
            LOG.info(
                'Sensor ' + str(msg.data['sensor']) +
                ' not required for MODIS PPS processing...')
            return False
    elif msg.data['platform_name'] in SUPPORTED_JPSS_SATELLITES:
        if msg.data['sensor'] not in ['viirs', ]:
            LOG.info(
                'Sensor ' + str(msg.data['sensor']) +
                ' not required for S-NPP/VIIRS PPS processing...')
            return False
    else:
        if msg.data['sensor'] not in NOAA_METOP_PPS_SENSORNAMES:
            LOG.info(
                'Sensor ' + str(msg.data['sensor']) + ' not required...')
            return False
        required_mw_sensors = REQUIRED_MW_SENSORS.get(
            msg.data['platform_name'])
        if (msg.data['sensor'] in required_mw_sensors and
                msg.data['data_processing_level'] != '1C'):
            if msg.data['data_processing_level'] == '1c':
                LOG.warning("Level should be in upper case!")
            else:
                LOG.info('Level not the required type for PPS for this sensor: ' +
                         str(msg.data['sensor']) + ' ' +
                         str(msg.data['data_processing_level']))
                return False

    # The orbit number is mandatory!
    orbit_number = int(msg.data['orbit_number'])
    LOG.debug("Orbit number: " + str(orbit_number))

    # sensor = (msg.data['sensor'])
    platform_name = msg.data['platform_name']

    if platform_name not in SATELLITE_NAME:
        LOG.warning("Satellite not supported: " + str(platform_name))
        return False

    if 'start_time' in msg.data:
        starttime = msg.data['start_time']
        sceneid = (str(platform_name) + '_' +
                   str(orbit_number) + '_' +
                   str(starttime.strftime('%Y%m%d%H%M')))
    else:
        starttime = None
        sceneid = (str(platform_name) + '_' +
                   str(orbit_number))

    LOG.debug("Scene identifier = " + str(sceneid))

    if sceneid not in files4pps:
        files4pps[sceneid] = []

    LOG.debug("level1_files = %s", level1_files)
    if platform_name in SUPPORTED_EOS_SATELLITES:
        for item in level1_files:
            fname = os.path.basename(item)
            LOG.debug("EOS level-1 file: %s", item)
            if (fname.startswith(GEOLOC_PREFIX[platform_name]) or
                    fname.startswith(DATA1KM_PREFIX[platform_name])):
                files4pps[sceneid].append(item)
    else:
        for item in level1_files:
            fname = os.path.basename(item)
            files4pps[sceneid].append(fname)

    LOG.debug("files4pps: %s", str(files4pps[sceneid]))
    if (msg.data['variant'] in ['EARS', ] and platform_name in SUPPORTED_METOP_SATELLITES):
        LOG.info("EARS Metop data. Only require the HRPT/AVHRR level-1b file to be ready!")
    elif (platform_name in SUPPORTED_METOP_SATELLITES or
          platform_name in SUPPORTED_NOAA_SATELLITES):
        if len(files4pps[sceneid]) < len(REQUIRED_MW_SENSORS[platform_name]) + 1:
            LOG.info("Not enough NOAA/Metop sensor data available yet...")
            return False
    elif platform_name in SUPPORTED_EOS_SATELLITES:
        if len(files4pps[sceneid]) < 2:
            LOG.info("Not enough MODIS level 1 files available yet...")
            return False

    if len(files4pps[sceneid]) > 10:
        LOG.info(
            "Number of level 1 files ready = " + str(len(files4pps[sceneid])))
        LOG.info("Scene = " + str(sceneid))
    else:
        LOG.info("Level 1 files ready: " + str(files4pps[sceneid]))

    # Clean the files4pps dict:
    LOG.debug("files4pps: " + str(files4pps))
    try:
        files4pps.pop(sceneid)
    except KeyError:
        LOG.warning("Failed trying to remove key " + str(sceneid) +
                    " from dictionary files4pps")
    LOG.debug("After cleaning: files4pps = " + str(files4pps))

    if msg.data['platform_name'] in SUPPORTED_PPS_SATELLITES:
        LOG.info(
            "This is a PPS supported scene. Start the PPS lvl2 processing!")
        LOG.info("Process the scene (sat, orbit) = " +
                 str(platform_name) + ' ' + str(orbit_number))

        return True


class FilePublisher(threading.Thread):

    """A publisher for the PPS result files. Picks up the return value from the
    pps_worker when ready, and publishes the files via posttroll"""

    def __init__(self, queue):
        threading.Thread.__init__(self)
        self.loop = True
        self.queue = queue
        self.jobs = {}

    def stop(self):
        """Stops the file publisher"""
        self.loop = False
        self.queue.put(None)

    def run(self):

        with Publish('pps_runner', 0, ['PPS', ]) as publisher:

            while self.loop:
                retv = self.queue.get()

                if retv != None:
                    LOG.info("Publish the files...")
                    publisher.send(retv)


class FileListener(threading.Thread):

    def __init__(self, queue):
        threading.Thread.__init__(self)
        self.loop = True
        self.queue = queue

    def stop(self):
        """Stops the file listener"""
        self.loop = False
        self.queue.put(None)

    def run(self):

        with posttroll.subscriber.Subscribe("", ['AAPP-HRPT', 'AAPP-PPS',
                                                 'EOS/1B', 'SDR/1B'],
                                            True) as subscr:

            for msg in subscr.recv(timeout=90):
                if not self.loop:
                    break

                # Check if it is a relevant message:
                if self.check_message(msg):
                    LOG.info("Put the message on the queue...")
                    LOG.debug("Message = " + str(msg))
                    self.queue.put(msg)

    def check_message(self, msg):

        if not msg:
            return False

        if ('platform_name' not in msg.data or
                'orbit_number' not in msg.data or
                'start_time' not in msg.data):
            LOG.warning("Message is lacking crucial fields...")
            return False

        if (msg.data['platform_name'] not in SUPPORTED_PPS_SATELLITES):
            LOG.info(str(msg.data['platform_name']) + ": " +
                     "Not a NOAA/Metop/S-NPP/Terra/Aqua scene. Continue...")
            return False

        return True


def check_threads(threads):
    """Scan all threads and join those that are finished (dead)"""

    # LOG.debug(str(threading.enumerate()))
    for i, thread in enumerate(threads):
        if thread.is_alive():
            LOG.info("Thread " + str(i) + " alive...")
        else:
            LOG.info(
                "Thread " + str(i) + " is no more alive...")
            thread.join()
            threads.remove(thread)

    return


def run_nwp_and_pps(scene, flens, publish_q, input_msg):
    """Run first the nwp-preparation and then pps. No parallel running here!"""

    prepare_nwp4pps(flens)
    pps_worker(scene, publish_q, input_msg)

    return


def prepare_nwp4pps(flens):
    """Prepare NWP data for pps"""

    starttime = datetime.utcnow() - timedelta(days=1)
    try:
        update_nwp(starttime, flens)
        LOG.info("Ready with nwp preparation")
        LOG.debug("Leaving prepare_nwp4pps...")
    except:
        LOG.exception("Something went wrong in update_nwp...")
        raise


def pps():
    """The PPS runner. Triggers processing of PPS main script once AAPP or CSPP
    is ready with a level-1 file"""

    LOG.info("*** Start the PPS level-2 runner:")

    LOG.info("First check if NWP data should be downloaded and prepared")
    now = datetime.utcnow()
    update_nwp(now - timedelta(days=1), NWP_FLENS)
    LOG.info("Ready with nwp preparation...")

    listener_q = Queue.Queue()
    publisher_q = Queue.Queue()

    pub_thread = FilePublisher(publisher_q)
    pub_thread.start()
    listen_thread = FileListener(listener_q)
    listen_thread.start()

    files4pps = {}
    thread_pool = ThreadPool(5)
    while True:

        try:
            msg = listener_q.get()
        except Queue.Empty:
            continue

        LOG.debug(
            "Number of threads currently alive: " + str(threading.active_count()))

        orbit_number = int(msg.data['orbit_number'])
        platform_name = msg.data['platform_name']
        starttime = msg.data['start_time']
        endtime = msg.data['end_time']

        satday = starttime.strftime('%Y%m%d')
        sathour = starttime.strftime('%H%M')
        sensors = SENSOR_LIST.get(platform_name, None)
        scene = {'platform_name': platform_name,
                 'orbit_number': orbit_number,
                 'satday': satday, 'sathour': sathour,
                 'starttime': starttime, 'endtime': endtime,
                 'sensor': sensors}

        status = ready2run(msg, files4pps)
        if status:

            LOG.info('Start a thread preparing the nwp data and run pps...')
            thread_pool.new_thread(message_uid(msg),
                                   target=run_nwp_and_pps, args=(scene, NWP_FLENS,
                                                                 publisher_q,
                                                                 msg))

            LOG.debug(
                "Number of threads currently alive: " + str(threading.active_count()))

    LOG.info("Wait till all threads are dead...")
    while True:
        workers_ready = True
        for thread in threads:
            if thread.is_alive():
                workers_ready = False

        if workers_ready:
            break

    pub_thread.stop()
    listen_thread.stop()

    return


if __name__ == "__main__":

    from logging import handlers

    if _PPS_LOG_FILE:
        ndays = int(OPTIONS["log_rotation_days"])
        ncount = int(OPTIONS["log_rotation_backup"])
        handler = handlers.TimedRotatingFileHandler(_PPS_LOG_FILE,
                                                    when='midnight',
                                                    interval=ndays,
                                                    backupCount=ncount,
                                                    encoding=None,
                                                    delay=False,
                                                    utc=True)

        handler.doRollover()
    else:
        handler = logging.StreamHandler(sys.stderr)

    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(fmt=_DEFAULT_LOG_FORMAT,
                                  datefmt=_DEFAULT_TIME_FORMAT)
    handler.setFormatter(formatter)
    logging.getLogger('').addHandler(handler)
    logging.getLogger('').setLevel(logging.DEBUG)
    logging.getLogger('posttroll').setLevel(logging.INFO)

    LOG = logging.getLogger('pps_runner')

    pps()
