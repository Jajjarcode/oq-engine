# -*- coding: utf-8 -*-
"""
Test the output of Loss Curve and Loss Ratio Curve as XML.

"""

from lxml import etree

import os
import unittest

from openquake import logs
from openquake.risk import engines
from openquake.output import risk as risk_output
from openquake import test
from openquake import shapes
from openquake import xml

log = logs.RISK_LOG

LOSS_XML_OUTPUT_FILE = 'loss-curves.xml'
LOSS_RATIO_XML_OUTPUT_FILE = 'loss-ratio-curves.xml'

#LOSS_SCHEMA_FILE = 'nrml.xsd'
NRML_SCHEMA_PATH = os.path.join(test.SCHEMA_DIR, xml.NRML_SCHEMA_FILE)
NRML_SCHEMA_PATH_OLD = os.path.join(test.SCHEMA_DIR, xml.NRML_SCHEMA_FILE_OLD)

TEST_CURVE = shapes.Curve([
     (0.0, 0.24105392741891271), (1280.0, 0.23487103910274165), 
     (2560.0, 0.22617525423987336), (3840.0, 0.21487350918336773), 
     (5120.0, 0.20130828974113113), (6400.0, 0.18625699583339819), 
     (8320.0, 0.16321642950263798), (10240.0, 0.14256493660395209), 
     (12160.0, 0.12605402369513649), (14080.0, 0.11348740908284834), 
     (16000.0, 0.103636128778507), (21120.0, 0.083400493736596762), 
     (26240.0, 0.068748634724073318), (31360.0, 0.059270296098829112), 
     (36480.0, 0.052738173061141945), (41600.0, 0.047128144517224253), 
     (49280.0, 0.039134392774233986), (56960.0, 0.032054271427490524),
     (64640.0, 0.026430436298219544), (72320.0, 0.022204123970325802),
     (80000.0, 0.018955490690565201), (90240.0, 0.01546384521034673), 
     (100480.0, 0.01253420544337625), (110720.0, 0.010091272074791734),
     (120960.0, 0.0081287946107584975), (131200.0, 0.0065806376555058105),
     (140160.0, 0.0054838330271587809), (149120.0, 0.0045616733509618087),
     (158080.0, 0.0037723441973124923), (167040.0, 0.0030934392072837253),
     (176000.0, 0.0025140588978909578), (189440.0, 0.0018158701863753069),
     (202880.0, 0.0012969740515868437), (216320.0, 0.00092183863089347865),
     (229760.0, 0.00065389822562465858), (243200.0, 0.00046282828510792824)])

    
class LossOutputTestCase(unittest.TestCase):
    """Confirm that XML output from risk engine is valid against schema,
    as well as correct given the inputs."""
    
    def setUp(self):
        self.path = test.test_output_file(LOSS_XML_OUTPUT_FILE)
        self.ratio_path = test.test_output_file(LOSS_RATIO_XML_OUTPUT_FILE)
        # self.schema_path = os.path.join(test.SCHEMA_DIR, LOSS_SCHEMA_FILE)
        self.schema_path = NRML_SCHEMA_PATH_OLD

        # Build up some sample loss curves here

        first_site = shapes.Site(10.0, 10.0)
        second_site = shapes.Site(10.0, 20.0)
        first_curve = TEST_CURVE
        second_curve = first_curve
        first_asset = {"AssetID" : "1711"}
        second_asset = {"AssetID" : "1712"}

        # Then serialize them to XML
        loss_curves = [(first_site, (first_curve, first_asset)), 
                        (second_site, (second_curve, second_asset))] 

        xml_writer = risk_output.LossCurveXMLWriter(self.path)
        xml_writer.serialize(loss_curves)
        
        xml_writer = risk_output.LossRatioCurveXMLWriter(self.ratio_path)
        xml_writer.serialize(loss_curves)

    # http://www.devcomments.com/error-restricting-complexType-list-parsing-official-GML-schema-at108628.htm
    # @test.skipit
    def test_xml_is_valid(self):
        # save the xml, and run schema validation on it
        xml_doc = etree.parse(self.path)
        loaded_xml = xml_doc.getroot()

        # Test that the doc matches the schema
        xmlschema = etree.XMLSchema(etree.parse(self.schema_path))
        xmlschema.assertValid(xml_doc)
    
    def test_loss_xml_is_correct(self):
        xml_doc = etree.parse(self.path)
        loaded_xml = xml_doc.getroot()

        xml_curve_pe = map(float, loaded_xml.find(".//"
                + xml.NRML_OLD + "LossCurvePE//"
                + xml.NRML_OLD + "Values").text.strip().split())
        xml_first_curve_value = loaded_xml.find(
                xml.NRML_OLD + "LossCurveList//" 
                + xml.NRML_OLD + "LossCurve//"
                + xml.NRML_OLD + "Values").text.strip().split()

        for idx, val in enumerate(TEST_CURVE.abscissae):
            self.assertAlmostEqual(val, float(xml_curve_pe[idx]), 6)
        for idx, val in enumerate(TEST_CURVE.ordinates):
            self.assertAlmostEqual(val, float(xml_first_curve_value[idx]), 6)

    # TODO(jmc): Test that the lat and lon are correct for each curve
    # Optionally, compare it to another XML file.

    def test_ratio_xml_is_correct(self):
        xml_doc = etree.parse(self.ratio_path)
        loaded_xml = xml_doc.getroot()

        xml_curve_pe = map(float, loaded_xml.find(".//"
                + xml.NRML_OLD + "LossRatioCurvePE//"
                + xml.NRML_OLD + "Values").text.strip().split())
        xml_first_curve_value = loaded_xml.find(
                xml.NRML_OLD + "LossRatioCurveList//" 
                + xml.NRML_OLD + "LossRatioCurve//"
                + xml.NRML_OLD + "Values").text.strip().split()

        for idx, val in enumerate(TEST_CURVE.abscissae):
            self.assertAlmostEqual(val, float(xml_curve_pe[idx]), 6)
        for idx, val in enumerate(TEST_CURVE.ordinates):
            self.assertAlmostEqual(val, float(xml_first_curve_value[idx]), 6)

