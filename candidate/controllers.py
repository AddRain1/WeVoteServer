# candidate/controllers.py
# Brought to you by We Vote. Be good.
# -*- coding: UTF-8 -*-

from .models import CandidateCampaignListManager, CandidateCampaignManager
from ballot.models import CANDIDATE
from config.base import get_environment_variable
from django.contrib import messages
from django.http import HttpResponse
from exception.models import handle_exception
from import_export_vote_smart.controllers import retrieve_and_match_candidate_from_vote_smart, \
    retrieve_candidate_photo_from_vote_smart
import json
from office.models import ContestOfficeManager
import requests
import wevote_functions.admin
from wevote_functions.functions import positive_value_exists

logger = wevote_functions.admin.get_logger(__name__)

WE_VOTE_API_KEY = get_environment_variable("WE_VOTE_API_KEY")
CANDIDATES_SYNC_URL = get_environment_variable("CANDIDATES_SYNC_URL")


def candidates_import_from_sample_file():
    """
    Get the json data, and either create new entries or update existing
    :return:
    """
    # Load saved json from local file
    logger.info("Loading CandidateCampaigns from local file")

    with open("candidate/import_data/candidate_campaigns_sample.json") as json_data:
        structured_json = json.load(json_data)

    return candidates_import_from_structured_json(structured_json)


def candidates_import_from_master_server(request, google_civic_election_id=''):
    """
    Get the json data, and either create new entries or update existing
    :return:
    """
    messages.add_message(request, messages.INFO, "Loading Candidates from We Vote Master servers")
    logger.info("Loading Candidates from We Vote Master servers")
    # Request json file from We Vote servers
    request = requests.get(CANDIDATES_SYNC_URL, params={
        "key": WE_VOTE_API_KEY,  # This comes from an environment variable
        "format":   'json',
        "google_civic_election_id": google_civic_election_id,
    })
    structured_json = json.loads(request.text)

    results = filter_candidates_structured_json_for_local_duplicates(structured_json)
    filtered_structured_json = results['structured_json']
    duplicates_removed = results['duplicates_removed']

    import_results = candidates_import_from_structured_json(filtered_structured_json)
    import_results['duplicates_removed'] = duplicates_removed

    return import_results


def filter_candidates_structured_json_for_local_duplicates(structured_json):
    """
    With this function, we remove candidates that seem to be duplicates, but have different we_vote_id's.
    We do not check to see if we have a matching office this routine -- that is done elsewhere.
    :param structured_json:
    :return:
    """
    duplicates_removed = 0
    filtered_structured_json = []
    candidate_list_manager = CandidateCampaignListManager()
    for one_candidate in structured_json:
        candidate_name = one_candidate['candidate_name'] if 'candidate_name' in one_candidate else ''
        google_civic_candidate_name = one_candidate['google_civic_candidate_name'] \
            if 'google_civic_candidate_name' in one_candidate else ''
        we_vote_id = one_candidate['we_vote_id'] if 'we_vote_id' in one_candidate else ''
        google_civic_election_id = \
            one_candidate['google_civic_election_id'] if 'google_civic_election_id' in one_candidate else ''
        contest_office_we_vote_id = \
            one_candidate['contest_office_we_vote_id'] if 'contest_office_we_vote_id' in one_candidate else ''
        politician_we_vote_id = one_candidate['politician_we_vote_id'] \
            if 'politician_we_vote_id' in one_candidate else ''
        candidate_twitter_handle = one_candidate['candidate_twitter_handle'] \
            if 'candidate_twitter_handle' in one_candidate else ''
        vote_smart_id = one_candidate['vote_smart_id'] if 'vote_smart_id' in one_candidate else ''
        maplight_id = one_candidate['maplight_id'] if 'maplight_id' in one_candidate else ''

        # Check to see if there is an entry that matches in all critical ways, minus the we_vote_id
        we_vote_id_from_master = we_vote_id

        results = candidate_list_manager.retrieve_possible_duplicate_candidates(
            candidate_name, google_civic_candidate_name, google_civic_election_id, contest_office_we_vote_id,
            politician_we_vote_id, candidate_twitter_handle, vote_smart_id, maplight_id,
            we_vote_id_from_master)

        if results['candidate_list_found']:
            # There seems to be a duplicate already in this database using a different we_vote_id
            duplicates_removed += 1
        else:
            filtered_structured_json.append(one_candidate)

    candidates_results = {
        'success':              True,
        'status':               "FILTER_CANDIDATES_FOR_DUPLICATES_PROCESS_COMPLETE",
        'duplicates_removed':   duplicates_removed,
        'structured_json':      filtered_structured_json,
    }
    return candidates_results


def candidates_import_from_structured_json(structured_json):
    candidate_campaign_manager = CandidateCampaignManager()
    candidates_saved = 0
    candidates_updated = 0
    candidates_not_processed = 0
    for one_candidate in structured_json:
        candidate_name = one_candidate['candidate_name'] if 'candidate_name' in one_candidate else ''
        we_vote_id = one_candidate['we_vote_id'] if 'we_vote_id' in one_candidate else ''
        google_civic_election_id = \
            one_candidate['google_civic_election_id'] if 'google_civic_election_id' in one_candidate else ''
        ocd_division_id = one_candidate['ocd_division_id'] if 'ocd_division_id' in one_candidate else ''
        contest_office_we_vote_id = \
            one_candidate['contest_office_we_vote_id'] if 'contest_office_we_vote_id' in one_candidate else ''

        # This routine imports from another We Vote server, so a contest_office_id doesn't come from import
        # Look up contest_office in this local database.
        # If we don't find a contest_office by we_vote_id, then we know the contest_office hasn't been imported
        # from another server yet, so we fail out.
        contest_office_manager = ContestOfficeManager()
        contest_office_id = contest_office_manager.fetch_contest_office_id_from_we_vote_id(
            contest_office_we_vote_id)

        if positive_value_exists(candidate_name) and positive_value_exists(google_civic_election_id) \
                and positive_value_exists(we_vote_id) and positive_value_exists(contest_office_id):
            proceed_to_update_or_create = True
        else:
            proceed_to_update_or_create = False
        if proceed_to_update_or_create:
            updated_candidate_campaign_values = {
                # Values we search against
                'google_civic_election_id': google_civic_election_id,
                'ocd_division_id': ocd_division_id,
                'contest_office_we_vote_id': contest_office_we_vote_id,
                'candidate_name': candidate_name,
                # The rest of the values
                'we_vote_id': we_vote_id,
                'maplight_id': one_candidate['maplight_id'] if 'maplight_id' in one_candidate else None,
                'vote_smart_id': one_candidate['vote_smart_id'] if 'vote_smart_id' in one_candidate else None,
                'contest_office_id': contest_office_id,  # Retrieved from above
                'politician_we_vote_id':
                    one_candidate['politician_we_vote_id'] if 'politician_we_vote_id' in one_candidate else '',
                'state_code': one_candidate['state_code'] if 'state_code' in one_candidate else '',
                'party': one_candidate['party'] if 'party' in one_candidate else '',
                'order_on_ballot': one_candidate['order_on_ballot'] if 'order_on_ballot' in one_candidate else 0,
                'candidate_url': one_candidate['candidate_url'] if 'candidate_url' in one_candidate else '',
                'photo_url': one_candidate['photo_url'] if 'photo_url' in one_candidate else '',
                'photo_url_from_maplight':
                    one_candidate['photo_url_from_maplight'] if 'photo_url_from_maplight' in one_candidate else '',
                'photo_url_from_vote_smart':
                    one_candidate['photo_url_from_vote_smart'] if 'photo_url_from_vote_smart' in one_candidate else '',
                'facebook_url': one_candidate['facebook_url'] if 'facebook_url' in one_candidate else '',
                'twitter_url': one_candidate['twitter_url'] if 'twitter_url' in one_candidate else '',
                'google_plus_url': one_candidate['google_plus_url'] if 'google_plus_url' in one_candidate else '',
                'youtube_url': one_candidate['youtube_url'] if 'youtube_url' in one_candidate else '',
                'google_civic_candidate_name':
                    one_candidate['google_civic_candidate_name']
                    if 'google_civic_candidate_name' in one_candidate else '',
                'candidate_email': one_candidate['candidate_email'] if 'candidate_email' in one_candidate else '',
                'candidate_phone': one_candidate['candidate_phone'] if 'candidate_phone' in one_candidate else '',
                'twitter_user_id': one_candidate['twitter_user_id'] if 'twitter_user_id' in one_candidate else '',
                'candidate_twitter_handle': one_candidate['candidate_twitter_handle']
                    if 'candidate_twitter_handle' in one_candidate else '',
                'twitter_name': one_candidate['twitter_name'] if 'twitter_name' in one_candidate else '',
                'twitter_location': one_candidate['twitter_location'] if 'twitter_location' in one_candidate else '',
                'twitter_followers_count': one_candidate['twitter_followers_count']
                    if 'twitter_followers_count' in one_candidate else '',
                'twitter_profile_image_url_https': one_candidate['twitter_profile_image_url_https']
                    if 'twitter_profile_image_url_https' in one_candidate else '',
                'twitter_description': one_candidate['twitter_description']
                    if 'twitter_description' in one_candidate else '',
                'wikipedia_page_id': one_candidate['wikipedia_page_id']
                    if 'wikipedia_page_id' in one_candidate else '',
                'wikipedia_page_title': one_candidate['wikipedia_page_title']
                    if 'wikipedia_page_title' in one_candidate else '',
                'wikipedia_photo_url': one_candidate['wikipedia_photo_url']
                    if 'wikipedia_photo_url' in one_candidate else '',
                'ballotpedia_page_title': one_candidate['ballotpedia_page_title']
                    if 'ballotpedia_page_title' in one_candidate else '',
                'ballotpedia_photo_url': one_candidate['ballotpedia_photo_url']
                    if 'ballotpedia_photo_url' in one_candidate else '',
                'ballot_guide_official_statement': one_candidate['ballot_guide_official_statement']
                    if 'ballot_guide_official_statement' in one_candidate else '',
            }
            results = candidate_campaign_manager.update_or_create_candidate_campaign(
                we_vote_id, google_civic_election_id, ocd_division_id,
                contest_office_id, contest_office_we_vote_id,
                candidate_name, updated_candidate_campaign_values)
        else:
            candidates_not_processed += 1
            results = {
                'success': False,
                'status': 'Required value missing, cannot update or create'
            }

        if results['success']:
            if results['new_candidate_created']:
                candidates_saved += 1
            else:
                candidates_updated += 1

    candidates_results = {
        'success':          True,
        'status':           "CANDIDATES_IMPORT_PROCESS_COMPLETE",
        'saved':            candidates_saved,
        'updated':          candidates_updated,
        'not_processed':    candidates_not_processed,
    }
    return candidates_results


def candidate_retrieve_for_api(candidate_id, candidate_we_vote_id):
    """
    Used by the api
    :param candidate_id:
    :param candidate_we_vote_id:
    :return:
    """
    # NOTE: Candidates retrieve is independent of *who* wants to see the data. Candidates retrieve never triggers
    #  a ballot data lookup from Google Civic, like voterBallotItems does

    if not positive_value_exists(candidate_id) and not positive_value_exists(candidate_we_vote_id):
        status = 'VALID_CANDIDATE_ID_AND_CANDIDATE_WE_VOTE_ID_MISSING'
        json_data = {
            'status':                   status,
            'success':                  False,
            'kind_of_ballot_item':      CANDIDATE,
            'id':                       candidate_id,
            'we_vote_id':               candidate_we_vote_id,
            'google_civic_election_id': 0,
        }
        return HttpResponse(json.dumps(json_data), content_type='application/json')

    candidate_manager = CandidateCampaignManager()
    if positive_value_exists(candidate_id):
        results = candidate_manager.retrieve_candidate_campaign_from_id(candidate_id)
        success = results['success']
        status = results['status']
    elif positive_value_exists(candidate_we_vote_id):
        results = candidate_manager.retrieve_candidate_campaign_from_we_vote_id(candidate_we_vote_id)
        success = results['success']
        status = results['status']
    else:
        status = 'VALID_CANDIDATE_ID_AND_CANDIDATE_WE_VOTE_ID_MISSING_2'  # It should be impossible to reach this
        json_data = {
            'status':                   status,
            'success':                  False,
            'kind_of_ballot_item':      CANDIDATE,
            'id':                       candidate_id,
            'we_vote_id':               candidate_we_vote_id,
            'google_civic_election_id': 0,
        }
        return HttpResponse(json.dumps(json_data), content_type='application/json')

    if success:
        candidate_campaign = results['candidate_campaign']
        json_data = {
            'status':                       status,
            'success':                      True,
            'kind_of_ballot_item':          CANDIDATE,
            'id':                           candidate_campaign.id,
            'we_vote_id':                   candidate_campaign.we_vote_id,
            'ballot_item_display_name':     candidate_campaign.display_candidate_name(),
            'candidate_photo_url':          candidate_campaign.candidate_photo_url(),
            'order_on_ballot':              candidate_campaign.order_on_ballot,
            'google_civic_election_id':     candidate_campaign.google_civic_election_id,
            'maplight_id':                  candidate_campaign.maplight_id,
            'contest_office_id':            candidate_campaign.contest_office_id,
            'contest_office_we_vote_id':    candidate_campaign.contest_office_we_vote_id,
            'politician_id':                candidate_campaign.politician_id,
            'politician_we_vote_id':        candidate_campaign.politician_we_vote_id,
            # 'google_civic_candidate_name': candidate_campaign.google_civic_candidate_name,
            'party':                        candidate_campaign.party_display(),
            'ocd_division_id':              candidate_campaign.ocd_division_id,
            'state_code':                   candidate_campaign.state_code,
            'candidate_url':                candidate_campaign.candidate_url,
            'facebook_url':                 candidate_campaign.facebook_url,
            'twitter_url':                  candidate_campaign.twitter_url,
            'twitter_handle':               candidate_campaign.fetch_twitter_handle(),
            'twitter_description':          candidate_campaign.twitter_description,
            'twitter_followers_count':      candidate_campaign.twitter_followers_count,
            'google_plus_url':              candidate_campaign.google_plus_url,
            'youtube_url':                  candidate_campaign.youtube_url,
            'candidate_email':              candidate_campaign.candidate_email,
            'candidate_phone':              candidate_campaign.candidate_phone,
        }
    else:
        json_data = {
            'status':                   status,
            'success':                  False,
            'kind_of_ballot_item':      CANDIDATE,
            'id':                       candidate_id,
            'we_vote_id':               candidate_we_vote_id,
            'google_civic_election_id': 0,
        }

    return HttpResponse(json.dumps(json_data), content_type='application/json')


def candidates_retrieve_for_api(office_id, office_we_vote_id):
    """
    Used by the api
    :param office_id:
    :param office_we_vote_id:
    :return:
    """
    # NOTE: Candidates retrieve is independent of *who* wants to see the data. Candidates retrieve never triggers
    #  a ballot data lookup from Google Civic, like voterBallotItems does

    if not positive_value_exists(office_id) and not positive_value_exists(office_we_vote_id):
        status = 'VALID_OFFICE_ID_AND_OFFICE_WE_VOTE_ID_MISSING'
        json_data = {
            'status':                   status,
            'success':                  False,
            'office_id':                office_id,
            'office_we_vote_id':        office_we_vote_id,
            'google_civic_election_id': 0,
            'candidate_list':           [],
        }
        return HttpResponse(json.dumps(json_data), content_type='application/json')

    candidate_list = []
    candidates_to_display = []
    google_civic_election_id = 0
    try:
        candidate_list_object = CandidateCampaignListManager()
        results = candidate_list_object.retrieve_all_candidates_for_office(office_id, office_we_vote_id)
        success = results['success']
        status = results['status']
        candidate_list = results['candidate_list']
    except Exception as e:
        status = 'FAILED candidates_retrieve. ' \
                 '{error} [type: {error_type}]'.format(error=e, error_type=type(e))
        handle_exception(e, logger=logger, exception_message=status)
        success = False

    if success:
        # Reset office_we_vote_id and office_id so we are sure that it matches what we pull from the database
        office_id = 0
        office_we_vote_id = ''
        for candidate in candidate_list:
            one_candidate = {
                'id':                           candidate.id,
                'we_vote_id':                   candidate.we_vote_id,
                'ballot_item_display_name':     candidate.display_candidate_name(),
                'candidate_photo_url':          candidate.candidate_photo_url(),
                'party':                        candidate.party_display(),
                'order_on_ballot':              candidate.order_on_ballot,
                'kind_of_ballot_item':          CANDIDATE,
            }
            candidates_to_display.append(one_candidate.copy())
            # Capture the office_we_vote_id and google_civic_election_id so we can return
            if not positive_value_exists(office_id) and candidate.contest_office_id:
                office_id = candidate.contest_office_id
            if not positive_value_exists(office_we_vote_id) and candidate.contest_office_we_vote_id:
                office_we_vote_id = candidate.contest_office_we_vote_id
            if not positive_value_exists(google_civic_election_id) and candidate.google_civic_election_id:
                google_civic_election_id = candidate.google_civic_election_id

        if len(candidates_to_display):
            status = 'CANDIDATES_RETRIEVED'
        else:
            status = 'NO_CANDIDATES_RETRIEVED'

        json_data = {
            'status':                   status,
            'success':                  True,
            'office_id':                office_id,
            'office_we_vote_id':        office_we_vote_id,
            'google_civic_election_id': google_civic_election_id,
            'candidate_list':           candidates_to_display,
        }
    else:
        json_data = {
            'status':                   status,
            'success':                  False,
            'office_id':                office_id,
            'office_we_vote_id':        office_we_vote_id,
            'google_civic_election_id': google_civic_election_id,
            'candidate_list':           [],
        }

    return HttpResponse(json.dumps(json_data), content_type='application/json')


def retrieve_candidate_photos(we_vote_candidate, force_retrieve=False):
    vote_smart_candidate_exists = False
    vote_smart_candidate_just_retrieved = False
    vote_smart_candidate_photo_exists = False
    vote_smart_candidate_photo_just_retrieved = False

    # Has this candidate already been linked to a Vote Smart candidate?
    candidate_retrieve_results = retrieve_and_match_candidate_from_vote_smart(we_vote_candidate, force_retrieve)

    if positive_value_exists(candidate_retrieve_results['vote_smart_candidate_id']):
        # Bring out the object that now has vote_smart_id attached
        we_vote_candidate = candidate_retrieve_results['we_vote_candidate']
        # Reach out to Vote Smart and retrieve photo URL
        photo_retrieve_results = retrieve_candidate_photo_from_vote_smart(we_vote_candidate)
        status = photo_retrieve_results['status']
        success = photo_retrieve_results['success']
        vote_smart_candidate_exists = True
        vote_smart_candidate_just_retrieved = candidate_retrieve_results['vote_smart_candidate_just_retrieved']

        if success:
            vote_smart_candidate_photo_exists = photo_retrieve_results['vote_smart_candidate_photo_exists']
            vote_smart_candidate_photo_just_retrieved = \
                photo_retrieve_results['vote_smart_candidate_photo_just_retrieved']
    else:
        status = candidate_retrieve_results['status'] + ' '
        status += 'RETRIEVE_CANDIDATE_PHOTOS_NO_CANDIDATE_MATCH'
        success = False

    results = {
        'success':                                      success,
        'status':                                       status,
        'vote_smart_candidate_exists':                  vote_smart_candidate_exists,
        'vote_smart_candidate_just_retrieved':          vote_smart_candidate_just_retrieved,
        'vote_smart_candidate_photo_just_retrieved':    vote_smart_candidate_photo_just_retrieved,
        'vote_smart_candidate_photo_exists':            vote_smart_candidate_photo_exists,
    }

    return results
