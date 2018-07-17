#!/usr/bin/env python
# coding=utf-8
# Copyright 2018 Criteo
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function

import datetime
import unittest
import uuid
import time

import elasticsearch_dsl

from biggraphite import glob_utils as bg_glob
from biggraphite import test_utils as bg_test_utils
from biggraphite.accessor import Aggregator, Metric, MetricMetadata, Retention
from biggraphite.drivers import elasticsearch as bg_elasticsearch
from biggraphite.test_utils_elasticsearch import HAS_ELASTICSEARCH

from tests.drivers.base_test_metadata import BaseTestAccessorMetadata


class ComponentFromNameTest(unittest.TestCase):

    def test_components_from_name_should_throw_AttributeError_on_None_path(self):
        with self.assertRaises(AttributeError):
            bg_elasticsearch._components_from_name(None)

    def test_components_from_name_should_create_array_by_splitting_on_dots(self):
        expected = ['a', 'b', 'c', 'd']
        result = bg_elasticsearch._components_from_name("a.b.c.d")
        self.assertEqual(result, expected)

    def test_components_from_name_should_ignore_double_dots(self):
        expected = ['a', 'b', 'c']
        result = bg_elasticsearch._components_from_name("a.b..c")
        self.assertEqual(result, expected)

    def test_components_from_name_should_handle_empty_strings(self):
        expected = []
        result = bg_elasticsearch._components_from_name("")
        self.assertEqual(result, expected)


class DocumentFromMetricTest(unittest.TestCase):

    def test_document_from_metric_should_throw_AttributeError_on_None_metric(self):
        with self.assertRaises(AttributeError):
            bg_elasticsearch.document_from_metric(None)

    def test_document_from_metric_should_build_a_document_from_a_metric(self):
        p0 = "foo"
        p1 = "bar"
        p2 = "baz"
        metric_name = "%s.%s.%s" % (p0, p1, p2)
        metric_id = uuid.uuid5(uuid.UUID("{00000000-1111-2222-3333-444444444444}"), metric_name)

        aggregator = Aggregator.maximum
        retention_str = "42*1s:43*60s"
        retention = Retention.from_string(retention_str)
        carbon_xfilesfactor = 0.5
        metadata = MetricMetadata(aggregator, retention, carbon_xfilesfactor)
        metric = Metric(metric_name, metric_id, metadata)

        document = bg_elasticsearch.document_from_metric(metric)

        self.__check_document_value(document, "depth", 2)
        self.__check_document_value(document, "uuid", metric_id)
        self.__check_document_value(document, "p0", p0)
        self.__check_document_value(document, "p1", p1)
        self.__check_document_value(document, "p2", p2)

        self.assertTrue("config" in document)
        document_config = document['config']
        self.__check_document_value(document_config, "aggregator", aggregator.name)
        self.__check_document_value(document_config, "retention", retention_str)
        self.__check_document_value(
            document_config,
            "carbon_xfilesfactor", "%f" % carbon_xfilesfactor
        )

        self.assertTrue("created_on" in document)
        self.assertTrue(isinstance(document['created_on'], datetime.datetime))
        self.assertTrue("updated_on" in document)
        self.assertTrue(isinstance(document['updated_on'], datetime.datetime))
        self.assertTrue("read_on" in document)
        self.assertEqual(document['read_on'], None)

    def __check_document_value(self, document, key, value):
        self.assertTrue(key in document)
        self.assertEqual(document[key], value)


class ParseSimpleComponentTest(unittest.TestCase):

    def test_any_sequence_should_have_no_constraint(self):
        self.assertEqual(
            bg_elasticsearch.parse_simple_component([bg_glob.AnySequence()]),
            (None, None)
        )

    def test_string_sequence_should_have_term_constraint(self):
        value = "foo"
        self.assertEqual(
            bg_elasticsearch.parse_simple_component([value]),
            ('term', value)
        )

    def test_CharNotIn_should_have_regex_constraint(self):
        value1 = "a"
        value2 = "b"
        self.assertEqual(
            bg_elasticsearch.parse_simple_component([bg_glob.CharNotIn([value1, value2])]),
            ('regexp', "[^ab]")
        )

    def test_CharIn_should_have_regex_constraint(self):
        value1 = "a"
        value2 = "b"
        self.assertEqual(
            bg_elasticsearch.parse_simple_component([bg_glob.CharIn([value1, value2])]),
            ('regexp', "[ab]")
        )

    def test_SequenceIn_should_have_terms_constraint_when_there_are_no_regexp_in_terms(self):
        values = ["a", "b"]
        self.assertEqual(
            bg_elasticsearch.parse_simple_component([bg_glob.SequenceIn(values)]),
            ('terms', values)
        )

    def test_SequenceIn_should_have_regexp_constraint_when_there_are_regexp_in_terms(self):
        values = ["a", "b*"]
        self.assertEqual(
            bg_elasticsearch.parse_simple_component([bg_glob.SequenceIn(values)]),
            ('regexp', "(a|b*)")
        )

    def test_AnyChar_should_have_wildcard_constraint(self):
        self.assertEqual(
            bg_elasticsearch.parse_simple_component([bg_glob.AnyChar()]),
            ('wildcard', '?')
        )

    def test_Globstar_component_should_trigger_Error(self):
        with self.assertRaisesRegexp(bg_elasticsearch.Error, "Unhandled type 'Globstar'"):
            bg_elasticsearch.parse_simple_component([bg_glob.Globstar()])

    def test_None_component_should_trigger_Error(self):
        with self.assertRaisesRegexp(bg_elasticsearch.Error, "Unhandled type 'None'"):
            bg_elasticsearch.parse_simple_component([None])


class ParseComplexComponentTest(unittest.TestCase):

    def setUp(self):
        self._wildcard_samples = [bg_glob.AnyChar(), bg_glob.AnySequence(), 'a']
        self._regexp_samples = [bg_glob.CharIn(['α', 'β']), bg_glob.SequenceIn(['γ', 'δ'])]

    def test_wildcard_component_should_be_parsed_as_wildcard(self):
        for wildcard_sample in self._wildcard_samples:
            parsed_type, _ = bg_elasticsearch.parse_complex_component([wildcard_sample])
            self.assertEqual(parsed_type, "wildcard",
                             "%s should be parsed as a wildcard" % type(wildcard_sample).__name__)

    def test_combination_of_wildcard_components_should_be_parsed_as_wildcard(self):
        for wildcard_sample_1 in self._wildcard_samples:
            for wildcard_sample_2 in self._wildcard_samples:
                parsed_type, _ = bg_elasticsearch.parse_complex_component([
                    wildcard_sample_1,
                    wildcard_sample_2
                ])
                self.assertEqual(parsed_type, "wildcard",
                                 "[%s, %s] should be parsed as a wildcard" % (
                                     type(wildcard_sample_1).__name__,
                                     type(wildcard_sample_2).__name__)
                                 )

    def test_regexp_component_should_be_parsed_as_regexp(self):
        for regexp_sample in self._regexp_samples:
            parsed_type, _ = bg_elasticsearch.parse_complex_component([regexp_sample])
            self.assertEqual(parsed_type, "regexp",
                             "%s should be parsed as a regexp" % type(regexp_sample).__name__)

    def test_combination_of_regexp_components_should_be_parsed_as_regexp(self):
        for regexp_sample_1 in self._regexp_samples:
            for regexp_sample_2 in self._regexp_samples:
                parsed_type, _ = bg_elasticsearch.parse_complex_component([
                    regexp_sample_1,
                    regexp_sample_2
                ])
                self.assertEqual(parsed_type, "regexp",
                                 "[%s, %s] should be parsed as a regexp" % (
                                     type(regexp_sample_1).__name__,
                                     type(regexp_sample_2).__name__)
                                 )

    def test_combination_of_regexp_and_wildcard_components_should_be_parsed_as_regexp(self):
        for regexp_sample in self._regexp_samples:
            for wildcard_sample in self._wildcard_samples:
                parsed_type, _ = bg_elasticsearch.parse_complex_component([
                    regexp_sample,
                    wildcard_sample
                ])
                self.assertEqual(parsed_type, "regexp",
                                 "[%s, %s] should be parsed as a regexp" % (
                                     type(regexp_sample).__name__,
                                     type(wildcard_sample).__name__)
                                 )

    def test_AnyChar_should_be_translated_into_a_question_mark(self):
        _, result = bg_elasticsearch.parse_complex_component([bg_glob.AnyChar()])
        self.assertEqual(result, "?")

    def test_AnySequence_should_be_translated_into_an_asterisk(self):
        _, result = bg_elasticsearch.parse_complex_component([bg_glob.AnySequence()])
        self.assertEqual(result, "*")

    def test_string_should_be_translated_into_itself(self):
        _, result = bg_elasticsearch.parse_complex_component(['a'])
        self.assertEqual(result, "a")

    def test_CharIn_should_be_translated_into_a_range(self):
        _, result = bg_elasticsearch.parse_complex_component([bg_glob.CharIn(['a', 'b'])])
        self.assertEqual(result, "[ab]")

    def test_CharNotIn_should_be_translated_into_a_range(self):
        _, result = bg_elasticsearch.parse_complex_component([bg_glob.CharNotIn(['a', 'b'])])
        self.assertEqual(result, "[^ab]")

    def test_SequenceIn_should_be_translated_into_an_enum_regexp(self):
        _, result = bg_elasticsearch.parse_complex_component([bg_glob.SequenceIn(['a', 'b'])])
        self.assertEqual(result, "(a|b)")

    def test_Globstar_should_be_translated_into_a_match_all_regexp(self):
        _, result = bg_elasticsearch.parse_complex_component([bg_glob.Globstar()])
        self.assertEqual(result, ".*")

    def test_combination_of_glob_expressions_should_be_concatenated(self):
        _, result = bg_elasticsearch.parse_complex_component([
            'foo',
            bg_glob.SequenceIn(['bar', 'baz']),
            bg_glob.CharNotIn(['a', 'b']),
            bg_glob.Globstar()
        ])
        self.assertEqual(result, "foo(bar|baz)[^ab].*")


class ElasticsearchTestAccessorMetadata(BaseTestAccessorMetadata):

    # FIXME (t.chataigner) some duplication with CassandraTestAccessorMetadata.
    def test_metrics_ttl_correctly_refreshed(self):
        metric1 = self.make_metric("a.b.c.d.e.f")
        self.accessor.create_metric(metric1)
        self.flush()

        # Setting up the moc function
        isUpdated = [False]

        def touch_document_moc(*args, **kwargs):
            isUpdated[0] = True

        old_touch_fn = self.accessor._ElasticSearchAccessor__touch_document
        self.accessor._ElasticSearchAccessor__touch_document = touch_document_moc

        time.sleep(2)
        self.accessor.get_metric(metric1.name, touch=True)
        self.assertEqual(isUpdated[0], False)

        old_ttl = self.accessor._ElasticSearchAccessor__updated_on_ttl_sec
        self.accessor._ElasticSearchAccessor__updated_on_ttl_sec = 1
        self.accessor.get_metric(metric1.name, touch=True)
        self.assertEqual(isUpdated[0], True)

        self.accessor._ElasticSearchAccessor__updated_on_ttl_sec = old_ttl
        self.accessor._ElasticSearchAccessor__touch_document = old_touch_fn

    def test_metric_is_updated_after_ttl(self):
        metric = self.make_metric("foo")
        self.accessor.create_metric(metric)
        self.flush()

        created_metric = self.accessor.get_metric(metric.name)
        time.sleep(2)

        old_ttl = self.accessor._ElasticSearchAccessor__updated_on_ttl_sec
        self.accessor._ElasticSearchAccessor__updated_on_ttl_sec = 1

        updated_metric = self.accessor.get_metric(metric.name, touch=True)

        self.assertNotEquals(created_metric.updated_on, updated_metric.updated_on)

        self.accessor._ElasticSearchAccessor__updated_on_ttl_sec = old_ttl

    def test_metric_is_recreated_if_index_has_changed(self):
        old_get_index_fn = self.accessor.get_index

        def get_index_mock(metric):
            return "testindex_2017-07-21"

        self.accessor.get_index = get_index_mock

        metric = self.make_metric("elasticsearch.test_metric_is_recreated_if_index_has_changed")
        self.accessor.create_metric(metric)
        self.flush()

        self.accessor.get_metric(metric.name)

        self.accessor.get_index = old_get_index_fn

        old_ttl = self.accessor._ElasticSearchAccessor__updated_on_ttl_sec
        self.accessor._ElasticSearchAccessor__updated_on_ttl_sec = 1
        time.sleep(2)

        self.accessor.get_metric(metric.name, touch=True)
        self.flush()

        search = elasticsearch_dsl.Search()
        search = search.using(self.accessor.client) \
            .index("testindex*") \
            .source(['uuid', 'config', 'created_on', 'updated_on', 'read_on']) \
            .filter('term', name=metric.name) \
            .sort({'updated_on': {'order': 'desc'}})

        responses = search.execute()
        self.assertEquals(2, responses.hits.total)
        self.assertEquals(self.accessor.get_index(metric), responses.hits[0].meta.index)
        self.assertEquals(get_index_mock(None), responses.hits[1].meta.index)

        self.accessor._ElasticSearchAccessor__updated_on_ttl_sec = old_ttl


@unittest.skipUnless(
    HAS_ELASTICSEARCH, "ES_HOME must be set.",
)
class TestAccessorWithElasticsearch(ElasticsearchTestAccessorMetadata,
                                    bg_test_utils.TestCaseWithAccessor):
    ACCESSOR_SETTINGS = {'driver': 'elasticsearch'}


if __name__ == "__main__":
    unittest.main()