# apis_v1/views/views_share.py
# Brought to you by We Vote. Be good.
# -*- coding: UTF-8 -*-
from share.controllers import shared_item_retrieve_for_api, shared_item_save_for_api
from config.base import get_environment_variable
from django.http import HttpResponse
import json
import wevote_functions.admin
from wevote_functions.functions import convert_to_bool, get_voter_device_id,  \
    is_speaker_type_organization, is_speaker_type_public_figure, positive_value_exists

logger = wevote_functions.admin.get_logger(__name__)

WE_VOTE_SERVER_ROOT_URL = get_environment_variable("WE_VOTE_SERVER_ROOT_URL")


def shared_item_retrieve_view(request):  # sharedItemRetrieve
    voter_device_id = get_voter_device_id(request)  # We standardize how we take in the voter_device_id
    destination_full_url = request.GET.get('destination_full_url', '')
    shared_item_code = request.GET.get('shared_item_code', '')
    shared_item_clicked = positive_value_exists(request.GET.get('shared_item_clicked', False))
    json_data = shared_item_retrieve_for_api(
        voter_device_id=voter_device_id,
        destination_full_url=destination_full_url,
        shared_item_code=shared_item_code,
        shared_item_clicked=shared_item_clicked,
    )
    return HttpResponse(json.dumps(json_data), content_type='application/json')


def shared_item_save_view(request):  # sharedItemSave
    voter_device_id = get_voter_device_id(request)  # We standardize how we take in the voter_device_id
    destination_full_url = request.GET.get('destination_full_url', '')
    ballot_item_we_vote_id = request.GET.get('ballot_item_we_vote_id', None)
    google_civic_election_id = request.GET.get('google_civic_election_id', None)
    is_ballot_share = positive_value_exists(request.GET.get('is_ballot_share', False))
    is_candidate_share = positive_value_exists(request.GET.get('is_candidate_share', False))
    is_measure_share = positive_value_exists(request.GET.get('is_measure_share', False))
    is_office_share = positive_value_exists(request.GET.get('is_office_share', False))
    json_data = shared_item_save_for_api(
        voter_device_id=voter_device_id,
        destination_full_url=destination_full_url,
        ballot_item_we_vote_id=ballot_item_we_vote_id,
        google_civic_election_id=google_civic_election_id,
        is_ballot_share=is_ballot_share,
        is_candidate_share=is_candidate_share,
        is_measure_share=is_measure_share,
        is_office_share=is_office_share,
    )
    return HttpResponse(json.dumps(json_data), content_type='application/json')

