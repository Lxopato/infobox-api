# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.core.exceptions import ObjectDoesNotExist

import requests
from requests.utils import quote

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError, APIException

import random

from decimal import Decimal

from infobox.models import Property, PageRank
from infobox.serializers import PropertySerializer


class PropertyViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows properties to be viewed or edited.
    """
    queryset = Property.objects.all()
    serializer_class = PropertySerializer


@api_view(['GET'])
def get_entity_info(request):
    """
    API endpoint to retrieve Wikidata information
    """
    entity_id = request.GET['id'] or ''
    lang = request.GET['lang'] or ''
    strategy = request.GET['strategy'] or ''
    size = 25

    if strategy not in ['baseline', 'frecuency', 'pagerank', 'multiplicative', 'sum']:
        raise ValidationError('A valid strategy must be specified (or the parameter must not be used)', code=400)
    if entity_id == '':
        raise ValidationError('An entity ID must be given (add id parameter)', code=400)
    if lang == '':
        raise ValidationError('A language must be specified (add lang parameter)', code=400)

    infobox = {}
    wikidata_prop = _get_wikidata_info(entity_id, lang)
    wikidata_headers = _get_headers(entity_id, lang)
    if wikidata_prop.status_code != 200:
        raise APIException("Error on Wikidata API", wikidata_prop.status_code)

    if wikidata_headers.status_code != 200:
        raise APIException("Error on Wikidata API", wikidata_headers.status_code)

    try:
        infobox['label'] = wikidata_headers.json()['results']['bindings'][0]['label']['value']
    except IndexError:
        infobox['label'] = 'No available label in Wikidata for this Language'
    try:
        if 'description' in wikidata_headers.json()['results']['bindings'][0].keys():
            infobox['description'] = wikidata_headers.json()['results']['bindings'][0]['description']['value']
    except IndexError:
        infobox['description'] = 'No available description in Wikidata for this Language'
    try:
        if 'picture' in wikidata_headers.json()['results']['bindings'][0].keys():
            infobox['image'] = wikidata_headers.json()['results']['bindings'][0]['picture']['value']
    except IndexError:
        pass
    if strategy == 'baseline':
        infobox['properties'] = _infobox_baseline(wikidata_prop.json().get('results').get('bindings'), size)

    elif strategy == 'frecuency':
        infobox['properties'] = _infobox_frecuency_count(wikidata_prop.json().get('results').get('bindings'), size)

    elif strategy == 'pagerank':
        infobox['properties'] = _infobox_page_rank(wikidata_prop.json().get('results').get('bindings'), size)

    elif strategy == 'multiplicative':
        infobox['properties'] = _infobox_multiplicative(wikidata_prop.json().get('results').get('bindings'), size)

    elif strategy == 'sum':
        infobox['properties'] = _infobox_sum(wikidata_prop.json().get('results').get('bindings'), size)

    return Response(infobox)


def _get_wikidata_info(entity_id, lang):
    # query = "SELECT ?pLabel ?prop ?val WHERE { wd:Q" + entity_id + " ?prop ?val . ?ps wikibase:directClaim ?prop . ?ps rdfs:label ?pLabel . FILTER((LANG(?pLabel)) = '" + lang + "')}"

    query = "SELECT ?pLabel ?prop ?val ?valLabel WHERE { wd:Q" + entity_id + " ?prop ?val . ?ps wikibase:directClaim ?prop . ?ps rdfs:label ?pLabel . SERVICE wikibase:label { bd:serviceParam wikibase:language '" + lang + "'. } FILTER((LANG(?pLabel)) = '" + lang + "' && (?prop != wdt:P18))}"

    return requests.get("https://query.wikidata.org/sparql?format=json&query="+quote(query))


def _get_headers(entity_id, lang):
    query = "SELECT ?label ?description ?picture WHERE { wd:Q" + entity_id + " rdfs:label ?label . wd:Q" + entity_id + " schema:description ?description. OPTIONAL { wd:Q" + entity_id + " wdt:P18 ?picture } OPTIONAL {wd:Q" + entity_id + " wdt:P41 ?picture } FILTER((LANG(?label)) = '" + lang + "' && (LANG(?description) = '" + lang + "'))} LIMIT 1"

    return requests.get("https://query.wikidata.org/sparql?format=json&query="+quote(query))


def _infobox_baseline(prop, n):
    random.shuffle(prop)
    return prop[:n]


def _infobox_frecuency_count(prop, n):
    for p in prop:
        p['prop']['frecuency'] = _get_frecuency_count(p.get('prop').get('value'))
        # p['prop']['norm_frecuency'] = _frecuency_count_normalization(p['prop']['frecuency'])
    return sorted(prop, key=lambda x: x.get('prop').get('frecuency'), reverse=True)[:n]


def _infobox_page_rank(prop, n):
    for p in prop:

        if p.get('val').get('type') == 'uri' and '/entity/Q' in p.get('val').get('value'):
            q_code = p.get('val').get('value')
            q_code = q_code.split('/entity/Q')[1]
            p['val']['rank'] = _get_pagerank(q_code)
        else:
            p['val']['rank'] = 0

        p['val']['norm_rank'] = _pagerank_normalization(p['val']['rank'])

    return sorted(prop, key=lambda x: x.get('val').get('rank'), reverse=True)[:n]


def _infobox_multiplicative(prop, n):
    for p in prop:
        p['prop']['frecuency'] = _get_frecuency_count(p.get('prop').get('value'))

        if p.get('val').get('type') == 'uri' and '/entity/Q' in p.get('val').get('value'):
            q_code = p.get('val').get('value')
            q_code = q_code.split('/entity/Q')[1]
            p['val']['rank'] = _get_pagerank(q_code)
        else:
            p['val']['rank'] = 0

        p['val']['norm_rank'] = _pagerank_normalization(p['val']['rank'])
        p['prop']['norm_frecuency'] = _frecuency_count_normalization(p['prop']['frecuency'])

        p['score'] = p['val']['norm_rank'] * Decimal(p['prop']['norm_frecuency'])
    return sorted(prop, key=lambda x: x.get('score'), reverse=True)[:n]


def _infobox_sum(prop, n):
    for p in prop:
        p['prop']['frecuency'] = _get_frecuency_count(p.get('prop').get('value'))

        if p.get('val').get('type') == 'uri' and '/entity/Q' in p.get('val').get('value'):
            q_code = p.get('val').get('value')
            q_code = q_code.split('/entity/Q')[1]
            p['val']['rank'] = _get_pagerank(q_code)
        else:
            p['val']['rank'] = 0

        p['val']['norm_rank'] = _pagerank_normalization(p['val']['rank'])
        p['prop']['norm_frecuency'] = _frecuency_count_normalization(p['prop']['frecuency'])

        p['score'] = p['val']['norm_rank'] + Decimal(p['prop']['norm_frecuency'])
    return sorted(prop, key=lambda x: x.get('score'), reverse=True)[:n]


def _get_frecuency_count(val):
    value = 0
    try:
        frecuency = Property.objects.get(identifier=val)
        value = frecuency.get_frecuency()
    except ObjectDoesNotExist:
        value = 0
    return value


def _get_pagerank(val):
    value = 0
    try:
        ranking = PageRank.objects.get(entity=val)
        value = ranking.get_page_rank()
    except ObjectDoesNotExist:
        value = 0
    return value


def _frecuency_count_normalization(n):
    max_val = 941678168
    min_val = 0
    return (float)(n - min_val)/(max_val - min_val)


def _pagerank_normalization(n):
    max_val = Decimal(0.04088949929711902659)
    min_val = Decimal(0)
    return (n - min_val)/(max_val - min_val)
