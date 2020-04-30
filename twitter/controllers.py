# twitter/controllers.py
# Brought to you by We Vote. Be good.
# -*- coding: UTF-8 -*-

# See also WeVoteServer/import_export_twitter/controllers.py for routines that manage incoming twitter data
import tweepy
from .models import TwitterLinkPossibility, TwitterUserManager
from ballot.controllers import figure_out_google_civic_election_id_voter_is_watching
from candidate.models import CandidateCampaign, CandidateCampaignListManager
from config.base import get_environment_variable
from datetime import timedelta
from django.utils.timezone import now
from django.db.models import Q
from election.models import ElectionManager
from office.models import ContestOfficeManager
from organization.models import OrganizationListManager
from wevote_functions.functions import convert_state_code_to_state_text, convert_state_code_to_utc_offset, \
    convert_to_int, positive_value_exists, POSITIVE_SEARCH_KEYWORDS, NEGATIVE_SEARCH_KEYWORDS, \
    POSITIVE_TWITTER_HANDLE_SEARCH_KEYWORDS, NEGATIVE_TWITTER_HANDLE_SEARCH_KEYWORDS
from wevote_settings.models import RemoteRequestHistory, RemoteRequestHistoryManager, RETRIEVE_POSSIBLE_TWITTER_HANDLES
from math import floor, log2
from re import sub
from time import time

TWITTER_CONSUMER_KEY = get_environment_variable("TWITTER_CONSUMER_KEY")
TWITTER_CONSUMER_SECRET = get_environment_variable("TWITTER_CONSUMER_SECRET")
TWITTER_ACCESS_TOKEN = get_environment_variable("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = get_environment_variable("TWITTER_ACCESS_TOKEN_SECRET")


def analyze_twitter_search_results(search_results, search_results_length, candidate_name,
                                   candidate_campaign, possible_twitter_handles_list):
    search_term = candidate_campaign.candidate_name
    state_code = candidate_campaign.state_code
    state_full_name = convert_state_code_to_state_text(state_code)

    for possible_candidate_index in range(search_results_length):
        one_result = search_results[possible_candidate_index]
        likelihood_score = 0

        # Increase the score with increased followers count
        if positive_value_exists(one_result.followers_count):
            #  125 followers =  0 points
            #  250 followers = 10 points
            #  500 followers = 20 points
            # 1000 followers = 30 points
            followers_likelihood = floor(10.0 * log2(one_result.followers_count / 125.0))
            if positive_value_exists(followers_likelihood):
                if followers_likelihood > 30:
                    likelihood_score += 30
                else:
                    likelihood_score += followers_likelihood

        # Check if name (or parts of name) are in Twitter name and handle
        name_found_in_name = False
        name_found_in_screen_name = False
        screen_name_handling_regex = r"[^a-zA-Z]"
        for name in candidate_name.values():
            if len(name) and name in one_result.name:
                likelihood_score += 10
                name_found_in_name = True
            if len(name) and sub(screen_name_handling_regex, "", name).lower() in \
                             sub(screen_name_handling_regex, "", one_result.screen_name).lower():
                likelihood_score += 10
                name_found_in_screen_name = True

        if not name_found_in_name:
            likelihood_score -= 30
        if not name_found_in_screen_name:
            likelihood_score -= 20

        # Check if state or state code is in location or description
        if one_result.location and positive_value_exists(state_full_name) and state_full_name in one_result.location:
            likelihood_score += 30
        elif one_result.location and positive_value_exists(state_code) and state_code in one_result.location:
            likelihood_score += 20

        if one_result.description and positive_value_exists(state_full_name) and \
                state_full_name in one_result.description:
            likelihood_score += 20
        if one_result.description and positive_value_exists(state_code) and \
                state_code in one_result.description:
            likelihood_score += 10

        # Check if user time zone is close to election/state time zone
        state_utc_offset = convert_state_code_to_utc_offset(state_code)
        if one_result.utc_offset and state_utc_offset and abs(state_utc_offset - one_result.utc_offset) > 7200:
            likelihood_score -= 30

        # Check if candidate's party is in description
        political_party = candidate_campaign.political_party_display()
        if one_result.description and positive_value_exists(political_party) and \
                political_party in one_result.description:
            likelihood_score += 20

        # Check (each word individually) if office name is in description
        office_name = candidate_campaign.contest_office_name
        if positive_value_exists(office_name) and one_result.description:
            office_name = office_name.split()
            office_found_in_description = False
            for word in office_name:
                if len(word) > 1 and word in one_result.description:
                    likelihood_score += 10
                    office_found_in_description = True
            if not office_found_in_description:
                likelihood_score -= 10

        # Increase the score for every positive twitter handle keyword we find
        for keyword in POSITIVE_TWITTER_HANDLE_SEARCH_KEYWORDS:
            if one_result.screen_name and keyword in one_result.screen_name.lower():
                likelihood_score += 20

        # Decrease the score for every negative twitter handle keyword we find
        for keyword in NEGATIVE_TWITTER_HANDLE_SEARCH_KEYWORDS:
            if one_result.screen_name and keyword in one_result.screen_name.lower():
                likelihood_score -= 20

        # Increase the score for every positive keyword we find
        for keyword in POSITIVE_SEARCH_KEYWORDS:
            if one_result.description and keyword in one_result.description.lower():
                likelihood_score += 5

        # Decrease the score for every negative keyword we find
        for keyword in NEGATIVE_SEARCH_KEYWORDS:
            if one_result.description and keyword in one_result.description.lower():
                likelihood_score -= 20

        # Decrease the score for inactive accounts
        try:
            time_last_active = one_result.status.created_at.timestamp()
            time_difference = time() - time_last_active
            if positive_value_exists(time_difference):
                #  30 days = 2,592,000 seconds
                #  30 days inactive =   0 points
                #  60 days inactive = -10 points
                # 120 days inactive = -20 points
                # 240 days inactive = -30 points (etc.)
                inactivity_likelihood = floor(10.0 * log2(time_difference / 2.592e6))
                if positive_value_exists(inactivity_likelihood):
                    if inactivity_likelihood > 60:
                        likelihood_score -= 60
                    else:
                        likelihood_score -= inactivity_likelihood
        except AttributeError:
            # 'User' object (one_result) has no attribute 'status'
            # So the account likely has no tweets
            likelihood_score -= 60

        if not positive_value_exists(likelihood_score):
            likelihood_score = 0

        current_candidate_twitter_info = {
            'search_term': search_term,
            'likelihood_score': likelihood_score,
            'twitter_json': one_result._json,
        }

        possible_twitter_handles_list.append(current_candidate_twitter_info)


def delete_possible_twitter_handles(candidate_campaign):
    status = ""
    twitter_user_manager = TwitterUserManager()

    if not candidate_campaign:
        status += "DELETE_POSSIBLE_TWITTER_HANDLES-CANDIDATE_MISSING "
        results = {
            'success':                  False,
            'status':                   status,
        }
        return results

    results = twitter_user_manager.delete_twitter_link_possibilities(candidate_campaign.we_vote_id)
    status += results['status']

    results = {
        'success':                  True,
        'status':                   status,
    }

    return results


def retrieve_possible_twitter_handles(candidate_campaign):
    status = ""
    success = True
    twitter_user_manager = TwitterUserManager()
    remote_request_history_manager = RemoteRequestHistoryManager()

    if not candidate_campaign:
        status = "RETRIEVE_POSSIBLE_TWITTER_HANDLES-CANDIDATE_MISSING "
        results = {
            'success':                  False,
            'status':                   status,
        }
        return results

    if positive_value_exists(candidate_campaign.contest_office_we_vote_id) and not \
            positive_value_exists(candidate_campaign.contest_office_name):
        contest_office_manager = ContestOfficeManager()
        results = contest_office_manager.retrieve_contest_office_from_we_vote_id(
            candidate_campaign.contest_office_we_vote_id)
        if results['contest_office_found']:
            contest_office = results['contest_office']
            try:
                candidate_campaign.contest_office_name = contest_office.office_name
                candidate_campaign.save()
            except Exception as e:
                status += "FAILED_TO_SAVE_CANDIDATE_CAMPAIGN: " + str(e) + " "

    search_term = candidate_campaign.candidate_name

    auth = tweepy.OAuthHandler(TWITTER_CONSUMER_KEY, TWITTER_CONSUMER_SECRET)
    auth.set_access_token(TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET)
    api = tweepy.API(auth)
    # results = {'possible_twitter_handles_list': []}
    possible_twitter_handles_list = []
    search_results = api.search_users(q=search_term, page=1)

    search_results.sort(key=lambda possible_candidate: possible_candidate.followers_count, reverse=True)
    search_results_found = len(search_results)

    name_handling_regex = r"[^ \w'-]"
    candidate_name = {
        'title':       sub(name_handling_regex, "", candidate_campaign.extract_title()),
        'first_name':  sub(name_handling_regex, "", candidate_campaign.extract_first_name()),
        'middle_name': sub(name_handling_regex, "", candidate_campaign.extract_middle_name()),
        'last_name':   sub(name_handling_regex, "", candidate_campaign.extract_last_name()),
        'suffix':      sub(name_handling_regex, "", candidate_campaign.extract_suffix()),
        'nickname':    sub(name_handling_regex, "", candidate_campaign.extract_nickname()),
    }

    analyze_twitter_search_results(search_results, search_results_found, candidate_name, candidate_campaign,
                                   possible_twitter_handles_list)

    # Also include search results omitting any single-letter initials and periods in name.
    # Example: "A." is ignored while "A.J." becomes "AJ"
    modified_search_term = ""
    modified_search_term_base = ""
    if len(candidate_name['first_name']) > 1:
        modified_search_term += candidate_name['first_name'] + " "
    if len(candidate_name['middle_name']) > 1:
        modified_search_term_base += candidate_name['middle_name'] + " "
    if len(candidate_name['last_name']) > 1:
        modified_search_term_base += candidate_name['last_name']
    if len(candidate_name['suffix']):
        modified_search_term_base += " " + candidate_name['suffix']
    modified_search_term += modified_search_term_base
    if search_term != modified_search_term:
        modified_search_results = api.search_users(q=modified_search_term, page=1)
        modified_search_results.sort(key=lambda possible_candidate: possible_candidate.followers_count, reverse=True)
        modified_search_results_found = len(modified_search_results)
        analyze_twitter_search_results(modified_search_results, modified_search_results_found,
                                       candidate_name, candidate_campaign, possible_twitter_handles_list)

    # If nickname exists, try searching with nickname instead of first name
    if len(candidate_name['nickname']):
        modified_search_term_2 = candidate_name['nickname'] + " " + modified_search_term_base
        modified_search_results_2 = api.search_users(q=modified_search_term_2, page=1)
        modified_search_results_2.sort(key=lambda possible_candidate: possible_candidate.followers_count, reverse=True)
        modified_search_results_2_found = len(modified_search_results_2)
        analyze_twitter_search_results(modified_search_results_2, modified_search_results_2_found,
                                       candidate_name, candidate_campaign, possible_twitter_handles_list)

    twitter_handles_found = bool(possible_twitter_handles_list)
    status += "NUMBER_POSSIBLE_TWITTER_HANDLES_FOUND: " + str(len(possible_twitter_handles_list)) + " "

    if twitter_handles_found:
        for possibility_result in possible_twitter_handles_list:
            save_twitter_user_results = twitter_user_manager.update_or_create_twitter_link_possibility(
                candidate_campaign.we_vote_id, possibility_result['twitter_json'],
                possibility_result['search_term'], possibility_result['likelihood_score'])
            if not save_twitter_user_results['success']:
                status += save_twitter_user_results['status']
                success = False

    # Create a record denoting that we have retrieved from Twitter for this candidate
    save_results_history = remote_request_history_manager.create_remote_request_history_entry(
        RETRIEVE_POSSIBLE_TWITTER_HANDLES, candidate_campaign.google_civic_election_id,
        candidate_campaign.we_vote_id, None, len(possible_twitter_handles_list), status)
    if not save_results_history['success']:
        status += save_results_history['status']
        success = False

    results = {
        'success':                  success,
        'status':                   status,
        'num_of_possibilities':     str(len(possible_twitter_handles_list)),
    }

    return results


def fetch_number_of_candidates_needing_twitter_search():
    election_manager = ElectionManager()
    office_manager = ContestOfficeManager()
    # Run Twitter account search and analysis on candidates without a linked or possible Twitter account
    candidate_queryset = CandidateCampaign.objects.using('readonly').all()
    # Limit this search to upcoming_elections only
    google_civic_election_id_list = []
    results = election_manager.retrieve_upcoming_elections()
    election_list = results['election_list']
    for one_election in election_list:
        google_civic_election_id_list.append(one_election.google_civic_election_id)
    office_visiting_list_we_vote_ids = office_manager.fetch_office_visiting_list_we_vote_ids(
        host_google_civic_election_id_list=google_civic_election_id_list)
    candidate_queryset = candidate_queryset.filter(
        Q(google_civic_election_id__in=google_civic_election_id_list) |
        Q(contest_office_we_vote_id__in=office_visiting_list_we_vote_ids))
    candidate_queryset = candidate_queryset.filter(
        Q(candidate_twitter_handle__isnull=True) | Q(candidate_twitter_handle=""))
    # Exclude candidates we have already have TwitterLinkPossibility data for
    try:
        twitter_possibility_list = TwitterLinkPossibility.objects.using('readonly'). \
            values_list('candidate_campaign_we_vote_id', flat=True).distinct()
        if len(twitter_possibility_list):
            candidate_queryset = candidate_queryset.exclude(we_vote_id__in=twitter_possibility_list)
    except Exception as e:
        pass
    # Exclude candidates we have requested information for in the last month
    try:
        # Exclude candidates searched for in the last month
        remote_request_query = RemoteRequestHistory.objects.using('readonly').all()
        one_month_of_seconds = 60 * 60 * 24 * 30  # 60 seconds, 60 minutes, 24 hours, 30 days
        one_month_ago = now() - timedelta(seconds=one_month_of_seconds)
        remote_request_query = remote_request_query.filter(datetime_of_action__gt=one_month_ago)
        remote_request_query = remote_request_query.filter(kind_of_action__iexact=RETRIEVE_POSSIBLE_TWITTER_HANDLES)
        remote_request_list = remote_request_query.values_list('candidate_campaign_we_vote_id', flat=True).distinct()
        if len(remote_request_list):
            candidate_queryset = candidate_queryset.exclude(we_vote_id__in=remote_request_list)
    except Exception as e:
        pass

    try:
        candidate_count = candidate_queryset.count()
    except Exception as e:
        candidate_count = 0

    return candidate_count


def retrieve_possible_twitter_handles_in_bulk(
        google_civic_election_id=0,
        state_code='',
        limit=0):
    status = ""
    success = True

    election_manager = ElectionManager()
    office_manager = ContestOfficeManager()
    # Run Twitter account search and analysis on candidates without a linked or possible Twitter account
    candidate_queryset = CandidateCampaign.objects.all()  # Cannot be readonly
    # Limit this search to upcoming_elections only
    google_civic_election_id_list = []
    if positive_value_exists(google_civic_election_id):
        google_civic_election_id_list.append(google_civic_election_id)
    else:
        results = election_manager.retrieve_upcoming_elections()
        election_list = results['election_list']
        for one_election in election_list:
            google_civic_election_id_list.append(one_election.google_civic_election_id)
    office_visiting_list_we_vote_ids = office_manager.fetch_office_visiting_list_we_vote_ids(
        host_google_civic_election_id_list=google_civic_election_id_list)
    candidate_queryset = candidate_queryset.filter(
        Q(google_civic_election_id__in=google_civic_election_id_list) |
        Q(contest_office_we_vote_id__in=office_visiting_list_we_vote_ids))
    candidate_queryset = candidate_queryset.filter(
        Q(candidate_twitter_handle__isnull=True) | Q(candidate_twitter_handle=""))
    if positive_value_exists(state_code):
        candidate_queryset = candidate_queryset.filter(state_code__iexact=state_code)

    # Exclude candidates we have already have TwitterLinkPossibility data for
    try:
        twitter_possibility_list = TwitterLinkPossibility.objects. \
            values_list('candidate_campaign_we_vote_id', flat=True).distinct()
        if len(twitter_possibility_list):
            candidate_queryset = candidate_queryset.exclude(we_vote_id__in=twitter_possibility_list)
    except Exception as e:
        status += "PROBLEM_RETRIEVING_TWITTER_LINK_POSSIBILITY " + str(e) + " "
    # Exclude candidates we have requested information for in the last month
    try:
        # Exclude candidates searched for in the last month
        remote_request_query = RemoteRequestHistory.objects.all()
        one_month_of_seconds = 60 * 60 * 24 * 30  # 60 seconds, 60 minutes, 24 hours, 30 days
        one_month_ago = now() - timedelta(seconds=one_month_of_seconds)
        remote_request_query = remote_request_query.filter(datetime_of_action__gt=one_month_ago)
        remote_request_query = remote_request_query.filter(kind_of_action__iexact=RETRIEVE_POSSIBLE_TWITTER_HANDLES)
        remote_request_list = remote_request_query.values_list('candidate_campaign_we_vote_id', flat=True).distinct()
        if len(remote_request_list):
            candidate_queryset = candidate_queryset.exclude(we_vote_id__in=remote_request_list)
    except Exception as e:
        status += "PROBLEM_RETRIEVING_TWITTER_LINK_POSSIBILITY " + str(e) + " "

    # Limit so we don't overwhelm Twitter's rate limiting
    # https://developer.twitter.com/en/docs/basics/rate-limits
    # GET users/search is limited to 900 per 15 minutes
    # Since we run one batch per minute, that means that 900 / 15 = 60
    # retrieve_possible_twitter_handles *might* search as many as 3 times per candidate, so we limit the number of
    # candidates we analyze to 20 per minute
    if positive_value_exists(limit):
        number_of_candidates_limit = limit
    else:
        number_of_candidates_limit = 20
    candidates_to_analyze = candidate_queryset.count()
    candidate_list = candidate_queryset[:number_of_candidates_limit]

    candidates_analyzed = 0
    status += "RETRIEVE_POSSIBLE_TWITTER_HANDLES_LOOP-TOTAL: " + str(candidates_to_analyze) + " "
    for one_candidate in candidate_list:
        # Twitter account search and analysis has not been run on this candidate yet
        results = retrieve_possible_twitter_handles(one_candidate)
        if results['success']:
            candidates_analyzed += 1
        status += results['status']

    results = {
        'success':                  success,
        'status':                   status,
        'candidates_to_analyze':    candidates_to_analyze,
        'candidates_analyzed':      candidates_analyzed,
    }

    return results


def twitter_identity_retrieve_for_api(twitter_handle, voter_device_id=''):  # twitterIdentityRetrieve
    status = "TWITTER_HANDLE_DOES_NOT_EXIST"  # Default to this
    success = True
    kind_of_owner = "TWITTER_HANDLE_DOES_NOT_EXIST"
    owner_we_vote_id = ''
    owner_id = 0
    google_civic_election_id = 0
    google_civic_election_id_voter_is_watching = 0
    twitter_description = ''
    twitter_followers_count = ''
    twitter_photo_url = ''
    we_vote_hosted_profile_image_url_large = ''
    we_vote_hosted_profile_image_url_medium = ''
    we_vote_hosted_profile_image_url_tiny = ''
    twitter_profile_banner_url_https = ''
    twitter_user_website = ''
    twitter_name = ''

    owner_found = False

    # Check Politician table for Twitter Handle
    # NOTE: It would be better to retrieve from the Politician, and then bring "up" information we need from the
    #  CandidateCampaign table. 2016-05-11 We haven't implemented Politician's yet though.

    # Check Candidate table
    if not positive_value_exists(owner_found):
        # Find out the election the voter is looking at
        results = figure_out_google_civic_election_id_voter_is_watching(voter_device_id)
        if positive_value_exists(results['google_civic_election_id']):
            google_civic_election_id_voter_is_watching = results['google_civic_election_id']
        state_code = ""
        candidate_name = ""

        candidate_list_manager = CandidateCampaignListManager()
        google_civic_election_id_list = [google_civic_election_id_voter_is_watching]
        candidate_results = candidate_list_manager.retrieve_candidates_from_non_unique_identifiers(
            google_civic_election_id_list, state_code, twitter_handle, candidate_name)
        if candidate_results['candidate_list_found']:
            candidate_list = candidate_results['candidate_list']

            # ...and then find the candidate entry for that election
            most_recent_candidate = candidate_list[0]
            for one_candidate in candidate_list:
                if google_civic_election_id_voter_is_watching == convert_to_int(one_candidate.google_civic_election_id):
                    kind_of_owner = "CANDIDATE"
                    owner_we_vote_id = one_candidate.we_vote_id
                    owner_id = one_candidate.id
                    google_civic_election_id = one_candidate.google_civic_election_id
                    owner_found = True
                    status = "OWNER_OF_THIS_TWITTER_HANDLE_FOUND-CANDIDATE"
                    # Now that we have candidate, break out of for-loop
                    break
            if not owner_found:
                kind_of_owner = "CANDIDATE"
                owner_we_vote_id = most_recent_candidate.we_vote_id
                owner_id = most_recent_candidate.id
                google_civic_election_id = most_recent_candidate.google_civic_election_id
                owner_found = True
                status = "OWNER_OF_THIS_TWITTER_HANDLE_FOUND-CANDIDATE"

    if not positive_value_exists(owner_found):
        organization_list_manager = OrganizationListManager()
        organization_results = organization_list_manager.retrieve_organizations_from_twitter_handle(
            twitter_handle=twitter_handle)
        if organization_results['organization_list_found']:
            organization_list = organization_results['organization_list']
            one_organization = organization_list[0]
            kind_of_owner = "ORGANIZATION"
            owner_we_vote_id = one_organization.we_vote_id
            owner_id = one_organization.id
            google_civic_election_id = 0
            owner_found = True
            status = "OWNER_OF_THIS_TWITTER_HANDLE_FOUND-ORGANIZATION"
            twitter_description = one_organization.twitter_description
            twitter_followers_count = one_organization.twitter_followers_count
            twitter_photo_url = one_organization.twitter_profile_image_url_https
            we_vote_hosted_profile_image_url_large = one_organization.we_vote_hosted_profile_image_url_large
            we_vote_hosted_profile_image_url_medium = one_organization.we_vote_hosted_profile_image_url_medium
            we_vote_hosted_profile_image_url_tiny = one_organization.we_vote_hosted_profile_image_url_tiny
            twitter_profile_banner_url_https = one_organization.twitter_profile_banner_url_https
            twitter_user_website = one_organization.organization_website
            twitter_name = one_organization.twitter_name

    # Reach out to Twitter (or our Twitter account cache) to retrieve some information we can display
    if not positive_value_exists(owner_found):
        twitter_user_manager = TwitterUserManager()
        twitter_user_id = 0
        twitter_results = \
            twitter_user_manager.retrieve_twitter_user_locally_or_remotely(twitter_user_id, twitter_handle)

        if twitter_results['twitter_user_found']:
            twitter_user = twitter_results['twitter_user']
            twitter_description = twitter_user.twitter_description
            twitter_followers_count = twitter_user.twitter_followers_count
            twitter_photo_url = twitter_user.twitter_profile_image_url_https
            we_vote_hosted_profile_image_url_large = twitter_user.we_vote_hosted_profile_image_url_large
            we_vote_hosted_profile_image_url_medium = twitter_user.we_vote_hosted_profile_image_url_medium
            we_vote_hosted_profile_image_url_tiny = twitter_user.we_vote_hosted_profile_image_url_tiny
            twitter_profile_banner_url_https = twitter_user.twitter_profile_banner_url_https
            twitter_user_website = twitter_user.twitter_url
            twitter_name = twitter_user.twitter_name
            kind_of_owner = "TWITTER_HANDLE_NOT_FOUND_IN_WE_VOTE"
            status = "TWITTER_HANDLE_NOT_FOUND_IN_WE_VOTE"

    results = {
        'status':                                   status,
        'success':                                  success,
        'twitter_handle':                           twitter_handle,
        'kind_of_owner':                            kind_of_owner,
        'owner_found':                              owner_found,
        'owner_we_vote_id':                         owner_we_vote_id,
        'owner_id':                                 owner_id,
        'google_civic_election_id':                 google_civic_election_id,
        'twitter_description':                      twitter_description,
        'twitter_followers_count':                  twitter_followers_count,
        'twitter_photo_url':                        twitter_photo_url,
        'we_vote_hosted_profile_image_url_large':   we_vote_hosted_profile_image_url_large,
        'we_vote_hosted_profile_image_url_medium':  we_vote_hosted_profile_image_url_medium,
        'we_vote_hosted_profile_image_url_tiny':    we_vote_hosted_profile_image_url_tiny,
        'twitter_profile_banner_url_https':         twitter_profile_banner_url_https,
        'twitter_user_website':                     twitter_user_website,
        'twitter_name':                             twitter_name,
    }
    return results
