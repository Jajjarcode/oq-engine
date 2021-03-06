# Copyright (c) 2010-2012, GEM Foundation.
#
# OpenQuake is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# OpenQuake is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with OpenQuake.  If not, see <http://www.gnu.org/licenses/>.

import json
import logging
import mock
import multiprocessing
import os.path
import socket
import threading
import time
import unittest

import kombu
import kombu.entity
import kombu.messaging

from openquake import java
from openquake import logs
from openquake.utils import config
from openquake.utils import stats

from tests.utils import helpers


class JavaLogsTestCase(unittest.TestCase):
    def setUp(self):
        self.jvm = java.jvm()
        self.handler = logging.handlers.BufferingHandler(capacity=float('inf'))
        self.python_logger = logging.getLogger('java')
        self.python_logger.addHandler(self.handler)
        self.python_logger.setLevel(logging.DEBUG)

        jlogger_class = self.jvm.JClass("org.apache.log4j.Logger")
        self.root_logger = jlogger_class.getRootLogger()
        self.other_logger = jlogger_class.getLogger('other_logger')

    def tearDown(self):
        self.python_logger.removeHandler(self.handler)
        self.python_logger.setLevel(logging.NOTSET)

    def test_error(self):
        self.root_logger.error('java error msg')
        [record] = self.handler.buffer
        self.assertEqual(record.levelno, logging.ERROR)
        self.assertEqual(record.levelname, 'ERROR')
        self.assertEqual(record.name, 'java')
        self.assertEqual(record.msg, 'java error msg')
        self.assertEqual(record.threadName, 'main')
        self.assertEqual(record.processName,
                         multiprocessing.current_process().name)

    def test_warning(self):
        self.other_logger.warn('warning message')
        [record] = self.handler.buffer
        self.assertEqual(record.levelno, logging.WARNING)
        self.assertEqual(record.levelname, 'WARNING')
        self.assertEqual(record.name, 'java.other_logger')
        self.assertEqual(record.msg, 'warning message')

    def test_debug(self):
        self.other_logger.debug('this is verbose debug info')
        [record] = self.handler.buffer
        self.assertEqual(record.levelno, logging.DEBUG)
        self.assertEqual(record.levelname, 'DEBUG')
        self.assertEqual(record.name, 'java.other_logger')
        self.assertEqual(record.msg, 'this is verbose debug info')

    def test_fatal(self):
        self.root_logger.fatal('something bad has happened')
        [record] = self.handler.buffer
        # java "fatal" records are mapped to python "critical" ones
        self.assertEqual(record.levelno, logging.CRITICAL)
        self.assertEqual(record.levelname, 'CRITICAL')
        self.assertEqual(record.name, 'java')
        self.assertEqual(record.msg, 'something bad has happened')

    def test_info(self):
        self.root_logger.info('information message')
        [record] = self.handler.buffer
        self.assertEqual(record.levelno, logging.INFO)
        self.assertEqual(record.levelname, 'INFO')
        self.assertEqual(record.name, 'java')
        self.assertEqual(record.msg, 'information message')

    def test_record_serializability(self):
        self.root_logger.info('whatever')
        [record] = self.handler.buffer
        # original args are tuple which becomes list
        # being encoded to json and back
        record.args = list(record.args)
        self.assertEqual(json.loads(json.dumps(record.__dict__)),
                         record.__dict__)

    def test_custom_level(self):
        # checking that logging with custom levels issues a warning but works

        # org.apache.log4j.Level doesn't allow to be instantiated directly
        # and jpype doesn't support subclassing java in python. that's why
        # in this test we just check JavaLoggingBridge without touching
        # java objects.
        class MockMessage(object):
            def getLevel(self):
                class Level(object):
                    def toInt(self):
                        return 12345
                return Level()

            @property
            def logger(self):
                class Logger(object):
                    def getParent(self):
                        return None
                return Logger()

            def getLocationInformation(self):
                class LocationInformation(object):
                    getFileName = lambda self: 'some/file'
                    getLineNumber = lambda self: '123'
                    getClassName = lambda self: 'someclassname'
                    getMethodName = lambda self: 'somemethod'
                return LocationInformation()

            getLoggerName = lambda self: 'root'
            getMessage = lambda self: 'somemessage'
            getThreadName = lambda self: 'somethread'

        java.JavaLoggingBridge().append(MockMessage())
        # we expect to have two messages logged in this case:
        # first is warning about unknown level used,
        # and second is the actual log message.
        [warning, record] = self.handler.buffer

        self.assertEqual(warning.levelno, logging.WARNING)
        self.assertEqual(warning.name, 'java')
        self.assertEqual(warning.getMessage(), 'unrecognised logging level ' \
                                               '12345 was used')

        self.assertEqual(record.levelno, 12345)
        self.assertEqual(record.levelname, 'Level 12345')
        self.assertEqual(record.name, 'java')
        self.assertEqual(record.msg, 'somemessage')
        self.assertEqual(record.pathname, 'some/file')
        self.assertEqual(record.lineno, 123)
        self.assertEqual(record.funcName, 'someclassname.somemethod')


class PythonAMQPLogTestCase(unittest.TestCase):
    LOGGER_NAME = 'tests.PythonAMQPLogTestCase'
    ROUTING_KEY = 'oq.job.None.%s.#' % LOGGER_NAME

    def setUp(self):
        self.amqp_handler = logs.AMQPHandler(level=logging.DEBUG)
        self.amqp_handler.set_job_id(None)

        self.log = logging.getLogger(self.LOGGER_NAME)
        self.log.setLevel(logging.DEBUG)
        self.log.addHandler(self.amqp_handler)

        cfg = config.get_section('amqp')
        self.connection = kombu.BrokerConnection(hostname=cfg.get('host'),
                                                 userid=cfg['user'],
                                                 password=cfg['password'],
                                                 virtual_host=cfg['vhost'])
        self.channel = self.connection.channel()
        self.exchange = kombu.entity.Exchange(cfg['exchange'], type='topic',
                                              channel=self.channel)
        self.queue = kombu.entity.Queue(exchange=self.exchange,
                                        channel=self.channel,
                                        routing_key=self.ROUTING_KEY,
                                        exclusive=True)
        self.queue.queue_declare()
        self.queue.queue_bind()
        self.consumer = kombu.messaging.Consumer(
            self.channel, self.queue, no_ack=True, auto_declare=False)
        self.producer = kombu.messaging.Producer(self.channel, self.exchange,
                                                 serializer='json')

    def tearDown(self):
        self.log.removeHandler(self.amqp_handler)
        if self.channel:
            self.channel.close()
        if self.connection:
            self.connection.close()

    def test_amqp_handler(self):
        messages = []

        def consume(data, msg):
            self.assertEqual(msg.properties['content_type'],
                             'application/json')
            messages.append((msg.delivery_info['routing_key'], data))

            if data['levelname'] == 'WARNING':
                # stop consuming when receive warning
                self.channel.close()
                self.channel = None
                self.connection.close()
                self.connection = None

        self.consumer.register_callback(consume)
        self.consumer.consume()

        self.log.getChild('child1').info('Info message %d %r', 42, 'payload')
        self.log.getChild('child2').warning('Warn message')

        while self.connection:
            self.connection.drain_events()

        self.assertEquals(2, len(messages))
        (info_key, info), (warning_key, warning) = messages

        self.assertEqual(info_key,
                         'oq.job.None.tests.PythonAMQPLogTestCase.child1')
        self.assertEqual(warning_key,
                         'oq.job.None.tests.PythonAMQPLogTestCase.child2')

        # checking info message
        self.assertAlmostEqual(info['created'], time.time(), delta=1)
        self.assertAlmostEqual(info['msecs'], (info['created'] % 1) * 1000)
        self.assertAlmostEqual(info['relativeCreated'] / 1000.,
                               time.time() - logging._startTime, delta=1)

        self.assertEqual(info['process'],
                         multiprocessing.current_process().ident)
        self.assertEqual(info['processName'],
                         multiprocessing.current_process().name)
        self.assertEqual(info['thread'], threading.current_thread().ident)
        self.assertEqual(info['threadName'], threading.current_thread().name)

        self.assertEqual(info['args'], [])
        self.assertEqual(info['msg'], "Info message 42 'payload'")

        self.assertEqual(info['name'], 'tests.PythonAMQPLogTestCase.child1')
        self.assertEqual(info['levelname'], 'INFO')
        self.assertEqual(info['levelno'], logging.INFO)

        self.assertEqual(info['module'], "logs_test")
        self.assertEqual(info['funcName'], 'test_amqp_handler')
        thisfile = __file__.rstrip('c')
        self.assertEqual(info['pathname'], thisfile)
        self.assertEqual(info['filename'], os.path.basename(thisfile))
        self.assertEqual(info['lineno'], 216)
        self.assertEqual(info['hostname'], socket.getfqdn())

        self.assertEqual(info['exc_info'], None)
        self.assertEqual(info['exc_text'], None)

        # warning message
        self.assertEqual(warning['name'], 'tests.PythonAMQPLogTestCase.child2')
        self.assertEqual(warning['levelname'], 'WARNING')
        self.assertEqual(warning['levelno'], logging.WARNING)

    def test_amqp_log_source(self):
        timeout = threading.Event()

        class _AMQPLogSource(logs.AMQPLogSource):
            stop_on_timeout = False

            def timeout_callback(self):
                timeout.set()
                if self.stop_on_timeout:
                    raise StopIteration()

        logsource = _AMQPLogSource('oq.testlogger.#', timeout=1.0)
        logsource_thread = threading.Thread(target=logsource.run)
        handler = logging.handlers.BufferingHandler(float('inf'))
        logger = logging.getLogger('oq.testlogger')
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            msg = dict(
                created=12345, msecs=321, relativeCreated=777,
                process=1, processName='prcs', thread=111, threadName='thrd',
                msg='message!', args=[],
                name='oq.testlogger.sublogger',
                levelname='INFO', levelno=logging.INFO,
                module='somemodule', funcName='somefunc', pathname='somepath',
                filename='somefile', lineno=262, hostname='apollo',
                exc_info=None, exc_text=None
            )
            self.producer.publish(msg.copy(),
                                  routing_key='oq.testlogger.sublogger')
            logsource_thread.start()
            timeout.wait()
            timeout.clear()
            # raising minimum level to make sure that info message
            # no longer can sneak in
            logger.setLevel(logging.WARNING)
            logsource.stop_on_timeout = True
            self.producer.publish(msg, routing_key='oq.testlogger.sublogger')
        finally:
            logger.removeHandler(handler)
            logsource.stop()
            logsource_thread.join()
        self.assertEqual(len(handler.buffer), 1)
        [record] = handler.buffer
        for key in msg:
            self.assertEqual(msg[key], getattr(record, key))


class InitLogsAmqpSendTestCase(unittest.TestCase):
    """Exercises the init_logs_amqp_send() function."""

    def setUp(self):
        super(InitLogsAmqpSendTestCase, self).setUp()
        self.root_handlers_orig = logging.root.handlers[:]
        logging.root.handlers = []

    def tearDown(self):
        super(InitLogsAmqpSendTestCase, self).setUp()
        logging.root.handlers = self.root_handlers_orig

    def test_init_logs_amqp_send_with_no_amqp_handler(self):
        """
        init_logs_amqp_send() will add an `AMQPHandler` instance to the
        root logger if none is present.
        """
        mm = mock.MagicMock(spec=kombu.messaging.Producer)
        with mock.patch.object(logs.AMQPHandler, "_initialize") as minit:
            minit.return_value = mm
            with helpers.patch("logging.root.addHandler") as mah:
                logs.init_logs_amqp_send("info", 321)
                self.assertEqual(1, mah.call_count)
                (single_arg,) = mah.call_args[0]
                self.assertTrue(isinstance(single_arg, logs.AMQPHandler))
        self.assertEqual(logging.root.level, logging.INFO)

    def test_init_logs_amqp_send_with_existing_amqp_handler(self):
        """
        init_logs_amqp_send() will not add more than one `AMQPHandler`
        instance to the root logger.
        """
        mm = mock.MagicMock(spec=kombu.messaging.Producer)
        with mock.patch.object(logs.AMQPHandler, "_initialize") as minit:
            minit.return_value = mm
            handler = logs.AMQPHandler()
            handler.set_job_id = mock.Mock()
            logging.root.handlers.append(handler)
            with helpers.patch("logging.root.addHandler") as mah:
                logs.init_logs_amqp_send("info", 322)
                self.assertEqual(0, mah.call_count)
                self.assertEqual(1, handler.set_job_id.call_count)
                self.assertEqual((322,), handler.set_job_id.call_args[0])

    def test_init_logs_amqp_send_changes_logging_level(self):
        """
        init_logs_amqp_send() will change the root level logger anyway.
        """
        mm = mock.MagicMock(spec=kombu.messaging.Producer)
        with mock.patch.object(logs.AMQPHandler, "_initialize") as minit:
            minit.return_value = mm
            handler = logs.AMQPHandler()
            logging.root.handlers.append(handler)
            handler.set_job_id = mock.Mock()

            logging.root.setLevel(logging.INFO)

            logs.init_logs_amqp_send("warning", 322)
            self.assertEqual(logging.root.level, logging.WARNING)

            logs.init_logs_amqp_send("debug", 323)
            self.assertEqual(logging.root.level, logging.DEBUG)

            logs.init_logs_amqp_send("error", 324)
            self.assertEqual(logging.root.level, logging.ERROR)


class LogPercentCompleteTestCase(unittest.TestCase):
    """Exercises the log_percent_complete() function."""

    def test_log_percent_complete_with_invalid_area(self):
        # nothing is reported, -1 is returned
        job_id = 11
        with mock.patch("openquake.logs.log_progress") as lpm:
            rv = logs.log_percent_complete(job_id, "invalid calculation")
            self.assertEqual(-1, rv)
            self.assertEqual(0, lpm.call_count)

    def test_log_percent_complete_with_same_percentage_value(self):
        # nothing is reported since the percentage complete value is the same
        job_id = 12
        stats.pk_set(job_id, "nhzrd_total", 100)
        stats.pk_set(job_id, "nhzrd_done", 12)
        stats.pk_set(job_id, "lvr", 12)

        with mock.patch("openquake.logs.log_progress") as lpm:
            rv = logs.log_percent_complete(job_id, "hazard")
            self.assertEqual(12, rv)
            self.assertEqual(0, lpm.call_count)

    def test_log_percent_complete_with_zero_percent_done(self):
        # nothing is reported since the percentage complete value is zero
        job_id = 13
        stats.pk_set(job_id, "nhzrd_total", 100)
        stats.pk_set(job_id, "nhzrd_done", 0)
        stats.pk_set(job_id, "lvr", -1)

        with mock.patch("openquake.logs.log_progress") as lpm:
            rv = logs.log_percent_complete(job_id, "hazard")
            self.assertEqual(0, rv)
            self.assertEqual(0, lpm.call_count)

    def test_log_percent_complete_with_new_percentage_value(self):
        # the percentage complete is reported since it exceeds the last value
        # reported
        job_id = 14
        stats.pk_set(job_id, "nhzrd_total", 100)
        stats.pk_set(job_id, "nhzrd_done", 20)
        stats.pk_set(job_id, "lvr", 12)

        with mock.patch("openquake.logs.log_progress") as lpm:
            rv = logs.log_percent_complete(job_id, "hazard")
            self.assertEqual(20, rv)
            self.assertEqual(1, lpm.call_count)
            self.assertEqual("hazard  20% complete",
                             lpm.call_args_list[0][0][0])

    def test_log_percent_complete_with_almost_same_percentage_value(self):
        # only 1 value is reported when the percentage complete value is
        # almost the same (12.6 versus 12).
        job_id = 12
        stats.pk_set(job_id, "nhzrd_total", 366)
        stats.pk_set(job_id, "nhzrd_done", 46)
        stats.pk_set(job_id, "lvr", 12)

        with mock.patch("openquake.logs.log_progress") as lpm:
            rv = logs.log_percent_complete(job_id, "hazard")
            self.assertEqual(12, rv)
            self.assertEqual(0, lpm.call_count)
