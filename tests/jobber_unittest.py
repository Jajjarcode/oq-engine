# -*- coding: utf-8 -*-
# vim: tabstop=4 shiftwidth=4 softtabstop=4

"""
These are the unit tests for the jobber module. At the moment, they contain
only tests for the basic underlying technologies (celery, memcached).
A test that asserts that a given computation result can reproduced is still
missing.
"""

import json
import os
import random
import time
import unittest

from opengem import logs
from opengem import kvs
from opengem import jobber
from opengem import shapes
from opengem.job import Job, EXPOSURE, INPUT_REGION
from opengem import test

import tests.tasks as test_tasks

logger = logs.LOG

TASK_NAME_SIMPLE = ["one", "two", "three", "four"]

WAIT_TIME_STEP_FOR_TASK_SECS = 0.5
MAX_WAIT_LOOPS = 10
SITE = shapes.Site(1.0, 1.0)
EXPOSURE_TEST_FILE = "ExposurePortfolioFile-test.xml"
REGION_EXPOSURE_TEST_FILE = "ExposurePortfolioFile-test.region"
REGION_TEST_FILE = "small.region"

class JobberTestCase(unittest.TestCase):

    def setUp(self):
        self.kvs_client = kvs.get_client(binary=False)

    def tearDown(self):
        pass

    def test_basic_asynchronous_processing_keep_order(self):

        # spawn four tasks that simply return a string with their name
        # result list will have them in the same order they are spawned
        results = []
        for curr_task_name in TASK_NAME_SIMPLE:
            results.append(test_tasks.simple_task_return_name.delay(
                curr_task_name))

        self.assertEqual(TASK_NAME_SIMPLE, 
                         [result.get() for result in results])

    def test_basic_asynchronous_processing_using_memcache(self):

        # spawn four tasks that simply return a string with their name
        # tasks write their results to memcached
        results = []
        for curr_task_name in TASK_NAME_SIMPLE:
            results.append(
                test_tasks.simple_task_return_name_to_memcache.apply_async(
                    args=[curr_task_name]))

        wait_for_celery_tasks(results)

        result_values = self.kvs_client.get_multi(TASK_NAME_SIMPLE)
        self.assertEqual(sorted(TASK_NAME_SIMPLE), 
                         sorted(result_values.values()))

    def test_async_memcache_serialize_list_and_dict(self):

        # spawn four tasks that return lists and dicts
        # tasks write their results to memcached
        # check if lists and dicts are serialized/deserialized correctly
        results = []
        for curr_task_name in TASK_NAME_SIMPLE:
            results.append(
                test_tasks.simple_task_list_dict_to_memcache.apply_async(
                    args=[curr_task_name]))

        wait_for_celery_tasks(results)

        expected_keys = []
        for name in TASK_NAME_SIMPLE:
            expected_keys.extend(["list.%s" % name, "dict.%s" % name])

        result_values = self.kvs_client.get_multi(expected_keys)

        expected_dict = {}
        for curr_key in sorted(expected_keys):
            if curr_key.startswith('list.'):
                expected_dict[curr_key] = str([curr_key[5:], curr_key[5:]])
            elif curr_key.startswith('dict.'):
                expected_dict[curr_key] = str({curr_key[5:]: curr_key[5:]})

        self.assertEqual(expected_dict, result_values)

    def test_async_memcache_serialize_json(self):

        # spawn four tasks that return json serializations of dicts
        # tasks write their results to memcached
        # check if objects are serialized/deserialized to/from json correctly
        results = []
        for curr_task_name in TASK_NAME_SIMPLE:
            results.append(
                test_tasks.simple_task_json_to_memcache.apply_async(
                    args=[curr_task_name]))

        wait_for_celery_tasks(results)

        result_values = self.kvs_client.get_multi(TASK_NAME_SIMPLE)

        expected_dict = {}
        for curr_key in TASK_NAME_SIMPLE:
            expected_dict[curr_key] = {
                "list.%s" % curr_key: [curr_key, curr_key], 
                "dict.%s" % curr_key: {curr_key: curr_key}}

        decoder = json.JSONDecoder()
        result_dict = {}
        for k, v in result_values.items():
            result_dict[k] = decoder.decode(v)

        self.assertEqual(expected_dict, result_dict)

    def test_prepares_blocks_using_the_exposure(self):
        job = Job({EXPOSURE: os.path.join(test.DATA_DIR, EXPOSURE_TEST_FILE)})
        
        job_manager = jobber.Jobber(job, True)
        blocks_keys = job_manager._partition()
        
        expected_block = jobber.Block((shapes.Site(9.15000, 45.16667),
                shapes.Site(9.15333, 45.12200), shapes.Site(9.14777, 45.17999),
                shapes.Site(9.15765, 45.13005), shapes.Site(9.15934, 45.13300),
                shapes.Site(9.15876, 45.13805)))
        
        self.assertEqual(1, len(blocks_keys))
        self.assertEqual(expected_block, jobber.Block.from_kvs(blocks_keys[0]))

    def test_prepares_blocks_using_the_exposure_and_filtering(self):
        job = Job({EXPOSURE: os.path.join(
                test.DATA_DIR, EXPOSURE_TEST_FILE),
                INPUT_REGION: os.path.join(
                test.DATA_DIR, REGION_EXPOSURE_TEST_FILE)})
    
        job_manager = jobber.Jobber(job, True)
        blocks_keys = job_manager._partition()

        expected_block = jobber.Block((shapes.Site(9.15765, 45.13005),
                shapes.Site(9.15934, 45.133), shapes.Site(9.15876, 45.13805)))

        self.assertEqual(1, len(blocks_keys))
        self.assertEqual(expected_block, jobber.Block.from_kvs(blocks_keys[0]))
    
    def test_prepares_blocks_using_the_input_region(self):
        region_path = os.path.join(test.DATA_DIR, REGION_TEST_FILE)
        job = Job({INPUT_REGION: region_path})

        expected_sites = []
        for site in shapes.Region.from_file(region_path):
            expected_sites.append(site)
    
        job_manager = jobber.Jobber(job, True)
        blocks_keys = job_manager._partition()

        self.assertEqual(1, len(blocks_keys))
        self.assertEqual(jobber.Block(expected_sites),
                jobber.Block.from_kvs(blocks_keys[0]))

    def test_with_no_partition_we_just_process_a_single_block(self):
        jobber.SITES_PER_BLOCK = 1
        
        # test exposure has 6 assets
        job = Job({EXPOSURE: os.path.join(
                test.DATA_DIR, EXPOSURE_TEST_FILE)})
        
        job_manager = jobber.Jobber(job, True)
        blocks_keys = job_manager._partition()

        # but we have 1 block instead of 6
        self.assertEqual(1, len(blocks_keys))


class BlockTestCase(unittest.TestCase):
    
    def test_a_block_has_a_unique_id(self):
        self.assertTrue(jobber.Block(()).id)
        self.assertTrue(jobber.Block(()).id != jobber.Block(()).id)

    def test_can_serialize_a_block_into_kvs(self):
        block = jobber.Block((SITE, SITE))
        block.to_kvs()

        self.assertEqual(block, jobber.Block.from_kvs(block.id))

class BlockSplitterTestCase(unittest.TestCase):
    
    def setUp(self):
        self.splitter = None
    
    def test_an_empty_set_produces_no_blocks(self):
        self.splitter = jobber.BlockSplitter(())
        self._assert_number_of_blocks_is(0)

    def test_splits_the_set_into_a_single_block(self):
        self.splitter = jobber.BlockSplitter((SITE,), 3)
        self._assert_number_of_blocks_is(1)

        self.splitter = jobber.BlockSplitter((SITE, SITE), 3)
        self._assert_number_of_blocks_is(1)

        self.splitter = jobber.BlockSplitter((SITE, SITE, SITE), 3)
        self._assert_number_of_blocks_is(1)

    def test_splits_the_set_into_multiple_blocks(self):
        self.splitter = jobber.BlockSplitter((SITE, SITE), 1)
        self._assert_number_of_blocks_is(2)

        self.splitter = jobber.BlockSplitter((SITE, SITE, SITE), 2)
        self._assert_number_of_blocks_is(2)

    def test_generates_the_correct_blocks(self):
        self.splitter = jobber.BlockSplitter((SITE, SITE, SITE), 3)
        expected_blocks = (jobber.Block((SITE, SITE, SITE)),)
        self._assert_blocks_are(expected_blocks)

        self.splitter = jobber.BlockSplitter((SITE, SITE, SITE), 2)
        expected_blocks = (jobber.Block((SITE, SITE)), jobber.Block((SITE,)))
        self._assert_blocks_are(expected_blocks)

    def test_splitting_with_region_intersection(self):
        region_constraint = shapes.RegionConstraint.from_simple(
                (0.0, 0.0), (2.0, 2.0))
        
        sites = (shapes.Site(1.0, 1.0), shapes.Site(1.5, 1.5),
            shapes.Site(2.0, 2.0), shapes.Site(3.0, 3.0))

        expected_blocks = (
                jobber.Block((shapes.Site(1.0, 1.0), shapes.Site(1.5, 1.5))),
                jobber.Block((shapes.Site(2.0, 2.0),)))

        self.splitter = jobber.BlockSplitter(sites, 2, constraint=region_constraint)
        self._assert_blocks_are(expected_blocks)

    def _assert_blocks_are(self, expected_blocks):
        for idx, block in enumerate(self.splitter):
            self.assertEqual(expected_blocks[idx], block)

    def _assert_number_of_blocks_is(self, number):
        counter = 0
        
        for block in self.splitter:
            counter += 1
        
        self.assertEqual(number, counter)


def wait_for_celery_tasks(celery_results, 
                          max_wait_loops=MAX_WAIT_LOOPS, 
                          wait_time=WAIT_TIME_STEP_FOR_TASK_SECS):
    """celery_results is a list of celery task result objects.
    This function waits until all tasks have finished.
    """

    # if a celery task has not yet finished, wait for a second
    # then check again
    counter = 0
    while (False in [result.ready() for result in celery_results]):
        counter += 1

        if counter > max_wait_loops:
            raise RuntimeError, "wait too long for celery worker threads"

        time.sleep(wait_time)
    
