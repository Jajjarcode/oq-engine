# -*- coding: utf-8 -*-
"""
Main jobber module
"""

import json
import os
import random
import time
import unittest

from opengem import flags
from opengem import identifiers
from opengem import logs
from opengem import memcached
from opengem import producer
from opengem import shapes

from opengem.risk import tasks

from opengem.output import geotiff
from opengem.output.risk import RiskXMLWriter

from opengem.parser import exposure
from opengem.parser import shaml_output
from opengem.parser import vulnerability


FLAGS = flags.FLAGS
logger = logs.LOG

LOSS_CURVES_OUTPUT_FILE = 'loss-curves-jobber.xml'

class Jobber(object):
    """The Jobber class is responsible to evaluate the configuration settings
    and to execute the computations in parallel tasks (using the celery
    framework and the message queue RabbitMQ).
    """
    def __init__(self, vulnerability_model_file, hazard_curve_file,
                 region_file, exposure_file, output_file, partition):

        self.vulnerability_model_file = vulnerability_model_file
        self.hazard_curve_file = hazard_curve_file
        self.region_file = region_file
        self.exposure_file = exposure_file
        self.output_file = output_file
        self.partition = partition

        self.memcache_client = None

        self.job_id_generator = identifiers.generate_id('job')
        self.block_id_generator = identifiers.generate_id('block')

        self._init()

    def run(self):
        """Core method of Jobber. It splits the requested computation
        in blocks and executes these as parallel tasks.
        """
        job_id = self.job_id_generator.next()
        logger.debug("running jobber, job_id = %s" % job_id)

        if self.partition is True:
            self._partition(job_id)
        else:
            block_id = self.block_id_generator.next()
            self._preload(job_id, block_id)
            self._execute(job_id, block_id)
            self._write_output_for_block(job_id, block_id)

        logger.debug("Jobber run ended")

    def _partition(self, job_id):

        # _partition() has to:
        # - get the full set of sites
        # - select a subset of these sites
        # - write the subset of sites to memcache, prepare a computation block
        pass

    def _execute(self, job_id, block_id):
        
        # execute celery task for risk, for given block with sites
        logger.debug("starting task block, block_id = %s" % block_id)

        # task compute_risk has return value 'True' (writes its results to
        # memcache).
        result = tasks.compute_risk.apply_async(args=[job_id, block_id])

        # TODO(fab): Wait until result has been computed. This has to be
        # changed if we run more tasks in parallel.
        result.get()

    def _write_output_for_block(self, job_id, block_id):
        """note: this is usable only for one block"""
        
        # produce output for one block
        loss_curves = []

        for (gridpoint, (site_lon, site_lat)) in \
            memcached.get_sites_from_memcache(
                self.memcache_client, job_id, block_id):

            key = identifiers.generate_product_key(job_id, 
                block_id, gridpoint, identifiers.LOSS_CURVE_KEY_TOKEN)
            loss_curve = self.memcache_client.get(key)
            loss_curves.append((shapes.Site(site_lon, site_lat), 
                                loss_curve))

        logger.debug("serializing loss_curves")
        output_generator = RiskXMLWriter(LOSS_CURVES_OUTPUT_FILE)
        output_generator.serialize(loss_curves)
        
        #output_generator = output.SimpleOutput()
        #output_generator.serialize(ratio_results)
        
        #output_generator = geotiff.GeoTiffFile(output_file, region_constraint.grid)
        #output_generator.serialize(losses_one_perc)

    def _init(self):
        
        # TODO(fab): find out why this works only with binary=False
        self.memcache_client = memcached.get_client(binary=False)
        self.memcache_client.flush_all()

    def _preload(self, job_id, block_id):

        # set region
        region_constraint = shapes.RegionConstraint.from_file(self.region_file)

        # TODO(fab): the cell size has to be determined from the configuration 
        region_constraint.cell_size = 1.0

        # load hazard curve file and write to memcache_client
        shaml_parser = shaml_output.ShamlOutputFile(self.hazard_curve_file)
        attribute_constraint = \
            producer.AttributeConstraint({'IMT' : 'MMI'})

        sites_hash_list = []

        for site, hazard_curve_data in shaml_parser.filter(
                region_constraint, attribute_constraint):

            gridpoint = region_constraint.grid.point_at(site)

            # store site hashes in memcache
            # TODO(fab): separate this from hazard curves. Regions of interest
            # should not be taken from hazard curve input, should be 
            # idependent from the inputs (hazard, exposure)
            sites_hash_list.append((str(gridpoint), 
                                   (site.longitude, site.latitude)))

            hazard_curve = shapes.FastCurve(zip(hazard_curve_data['IML'], 
                                                hazard_curve_data['Values']))

            memcache_key_hazard = identifiers.generate_product_key(
                job_id, block_id, gridpoint, "hazard_curve")

            logger.debug("Loading hazard curve %s at %s, %s" % (
                        hazard_curve, site.latitude,  site.longitude))

            success = self.memcache_client.set(memcache_key_hazard, 
                hazard_curve.to_json())

            if success is not True:
                raise ValueError(
                    "jobber: cannot write hazard curve to memcache")

        # write site hashes to memcache (JSON)
        memcache_key_sites = identifiers.generate_product_key(
                job_id, block_id, None, "sites")
        success = memcached.set_value_json_encoded(self.memcache_client, 
                memcache_key_sites, sites_hash_list)
        if not success:
            raise ValueError(
                "jobber: cannot write sites to memcache")
        
        # load assets and write to memcache
        exposure_parser = exposure.ExposurePortfolioFile(self.exposure_file)
        for site, asset in exposure_parser.filter(region_constraint):
            gridpoint = region_constraint.grid.point_at(site)

            memcache_key_asset = identifiers.generate_product_key(
                job_id, block_id, gridpoint, "exposure")

            logger.debug("Loading asset %s at %s, %s" % (asset,
                site.longitude,  site.latitude))

            success = memcached.set_value_json_encoded(self.memcache_client, 
                memcache_key_asset, asset)
            if not success:
                raise ValueError(
                    "jobber: cannot write asset to memcache")

        # load vulnerability and write to memcache
        vulnerability.load_vulnerability_model(job_id,
            self.vulnerability_model_file)



