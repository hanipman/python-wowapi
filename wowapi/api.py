import logging
from datetime import datetime, timedelta

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from requests.packages.urllib3.util.retry import Retry

from .exceptions import WowApiException, WowApiOauthException

logger = logging.getLogger('wowapi')
logger.addHandler(logging.NullHandler())


class WowApi(object):

    __base_url = '{0}.api.blizzard.com'

    def __init__(self, client_id, client_secret, retry_conn_failures=False):
        self._client_id = client_id
        self._client_secret = client_secret

        self._session = requests.Session()

        # Use default retry setup
        if retry_conn_failures:
            self.retry_conn_failures()

        self._access_tokens = {}

    def _utcnow(self):
        return datetime.utcnow()

    def retry_conn_failures(self, total=5, backoff_factor=1,
                            status_forcelist=[443, 500, 502, 503, 504]):
        # Allows a user to control how retries function
        retries = Retry(total=total, backoff_factor=backoff_factor,
                        status_forcelist=status_forcelist)
        self._session.mount('http://', HTTPAdapter(max_retries=retries))
        self._session.mount('https://', HTTPAdapter(max_retries=retries))

    def _get_client_credentials(self, region):
        path = '/oauth/token?grant_type=client_credentials&client_id={0}&client_secret={1}'.format(
            self._client_id, self._client_secret
        )

        url = 'https://{0}.battle.net{1}'.format(region, path)
        if region == 'cn':
            url = 'https://www.battlenet.com.cn{0}'.format(path)

        logger.info('Fetching new token from: {0}'.format(url))

        now = self._utcnow()
        try:
            response = self._session.get(url)
        except RequestException as exc:
            logger.exception(str(exc))
            raise WowApiOauthException(str(exc))

        if not response.ok:
            msg = 'Invalid response - {0} for {1}'.format(response.status_code, url)
            logger.warning(msg)
            raise WowApiOauthException(msg)

        try:
            json = response.json()
        except Exception:
            msg = 'Invalid Json in OAuth request: {0} for {1}'.format(response.content, url)
            logger.exception(msg)
            raise WowApiOauthException(msg)

        token = json['access_token']
        expiration = now + timedelta(seconds=json['expires_in'])
        logger.info('New token {0} expires at {1} UTC'.format(token, expiration))

        self._access_tokens[region] = {
            'token': token,
            'expiration': expiration
        }

    def get_data_resource(self, url, region):
        params = {'access_token': self._access_tokens.get(region, {}).get('token', '')}
        return self._handle_request(url, region, params=params)

    def _handle_request(self, url, region, **kwargs):
        try:
            response = self._session.get(url, **kwargs)
        except RequestException as exc:
            logger.exception(str(exc))
            raise WowApiException(str(exc))

        if not response.ok:
            # get a new token and try request again
            if response.status_code == 401:
                logger.info('Access token invalid. Fetching new token..')
                self._get_client_credentials(region)
                return self._handle_request(url, region, **kwargs)

            msg = 'Invalid response - {0} - {1}'.format(url, response.status_code)
            logger.warning(msg)
            raise WowApiException(msg)

        try:
            return response.json()
        except Exception:
            msg = 'Invalid Json: {0} for {1}'.format(response.content, url)
            logger.exception(msg)
            raise WowApiException(msg)

    def get_resource(self, resource, region, *args, **filters):
        resource = resource.format(*args)

        base_url = self.__base_url.format(region)
        if region == 'cn':
            base_url = 'www.gateway.battlenet.com.cn'

        url = 'https://{0}/{1}'.format(base_url, resource)

        # fetch access token on first run for region
        if region not in self._access_tokens:
            logger.info('Fetching access token..')
            self._get_client_credentials(region)
        else:
            now = self._utcnow()
            # refresh access token if expiring in the next 30 seconds.
            # this protects against the rare occurrence of hitting
            # the API right as your token expires, causing errors.
            if now >= self._access_tokens[region]['expiration'] - timedelta(seconds=30):
                logger.info('Access token expired. Fetching new token..')
                self._get_client_credentials(region)

        filters['access_token'] = self._access_tokens[region]['token']
        logger.info('Requesting resource: {0} with parameters: {1}'.format(url, filters))
        return self._handle_request(url, region, params=filters)

    def get_oauth_profile(self, region):
        """
        World of Warcraft Profile API - data about wow profile for oauth token

        >>> WowApi.get_oauth_profile('us')
        """
        return self.get_resource('wow/user/characters', region)

    def get_achievement(self, region, id, **filters):
        """
        Achievement API

        >>> WowApi.get_achievement('us', 2144, locale='pt_BR')
        """
        return self.get_resource('wow/achievement/{0}', region, *[id], **filters)

    def get_auctions(self, region, realm_slug, **filters):
        """
        Auction API data status
        """
        return self.get_resource('wow/auction/data/{0}', region, *[realm_slug], **filters)

    def get_bosses(self, region, **filters):
        """
        Boss API - Master list of bosses
        """
        return self.get_resource('wow/boss/', region, **filters)

    def get_boss(self, region, id, **filters):
        """
        Boss API - Boss details
        """
        return self.get_resource('wow/boss/{0}', region, *[id], **filters)

    def get_realm_leaderboard(self, region, realm, **filters):
        """
        Challenge Mode API - realm leaderboard
        """
        return self.get_resource('wow/challenge/{0}', region, *[realm], **filters)

    def get_region_leaderboard(self, region, **filters):
        """
        Challenge Mode API - region leaderboard
        """
        return self.get_resource('wow/challenge/region', region, **filters)

    def get_character_profile(self, region, realm, character_name, **filters):
        """
        Character Profile API - base info or specific comma separated fields as filters

        >>> api = WowApi('client-id', 'client-secret')
        >>> api.get_character_profile('eu', 'khadgar', 'patchwerk')
        >>> api.get_character_profile('eu', 'khadgar', 'patchwerk', locale='en_GB', fields='guild,mounts')
        """  # noqa
        return self.get_resource(
            'wow/character/{0}/{1}', region, *[realm, character_name], **filters
        )

    def get_guild_profile(self, region, realm, guild_name, **filters):
        """
        Guild Profile API - base info or specific comma separated fields as filters

        >>> api = WowApi('client-id', 'client-secret')
        >>> api.get_guild_profile('eu', 'khadgar')
        >>> api.get_guild_profile('eu', 'khadgar', locale='en_GB', fields='achievements,challenge')
        """
        return self.get_resource('wow/guild/{0}/{1}', region, *[realm, guild_name], **filters)

    def get_item(self, region, id, **filters):
        """
        Item API - detail item
        """
        return self.get_resource('wow/item/{0}', region, *[id], **filters)

    def get_item_set(self, region, id, **filters):
        """
        Item API - detail item set
        """
        return self.get_resource('wow/item/set/{0}', region, *[id], **filters)

    def get_mounts(self, region, **filters):
        """
        Mounts API - all supported mounts
        """
        return self.get_resource('wow/mount/', region, **filters)

    def get_pets(self, region, **filters):
        """
        Pets API - all supported pets
        """
        return self.get_resource('wow/pet/', region, **filters)

    def get_pet_ability(self, region, id, **filters):
        """
        Pets API - pet ability details
        """
        return self.get_resource('wow/pet/ability/{0}', region, *[id], **filters)

    def get_pet_species(self, region, id, **filters):
        """
        Pets API - pet species details
        """
        return self.get_resource('wow/pet/species/{0}', region, *[id], **filters)

    def get_pet_stats(self, region, id, **filters):
        """
        Pets API - pet stats details
        """
        return self.get_resource('wow/pet/stats/{0}', region, *[id], **filters)

    def get_leaderboards(self, region, bracket, **filters):
        """
        Pvp API - pvp bracket leaderboard and rbg
        """
        return self.get_resource('wow/leaderboard/{0}', region, *[bracket], **filters)

    def get_quest(self, region, id, **filters):
        """
        Quest API - metadata for quests
        """
        return self.get_resource('wow/quest/{0}', region, *[id], **filters)

    def get_realm_status(self, region, **filters):
        """
        Realm Status API - realm status for region
        """
        return self.get_resource('wow/realm/status', region, **filters)

    def get_recipe(self, region, id, **filters):
        """
        Recipe API - recipe details
        """
        return self.get_resource('wow/recipe/{0}', region, *[id], **filters)

    def get_spell(self, region, id, **filters):
        """
        Spell API - spell details
        """
        return self.get_resource('wow/spell/{0}', region, *[id], **filters)

    def get_characters(self, region, **filters):
        """
        User API - List of characters of account

        >>> WowApi.get_characters('us')
        """
        return self.get_resource('wow/user/characters', region, **filters)

    def get_zones(self, region, **filters):
        """
        Zone API - master list
        """
        return self.get_resource('wow/zone/', region, **filters)

    def get_zone(self, region, id, **filters):
        """
        Zone API - detail zone
        """
        return self.get_resource('wow/zone/{0}', region, *[id], **filters)

    def get_battlegroups(self, region, **filters):
        """
        Data Resources API - all battlegroups
        """
        return self.get_resource('wow/data/battlegroups/', region, **filters)

    def get_character_races(self, region, **filters):
        """
        Data Resources API - all character races
        """
        return self.get_resource('wow/data/character/races', region, **filters)

    def get_character_classes(self, region, **filters):
        """
        Data Resources API - all character classes
        """
        return self.get_resource('wow/data/character/classes', region, **filters)

    def get_character_achievements(self, region, **filters):
        """
        Data Resources API - all character achievements
        """
        return self.get_resource('wow/data/character/achievements', region, **filters)

    def get_guild_rewards(self, region, **filters):
        """
        Data Resources API - all guild rewards
        """
        return self.get_resource('wow/data/guild/rewards', region, **filters)

    def get_guild_perks(self, region, **filters):
        """
        Data Resources API - all guild perks
        """
        return self.get_resource('wow/data/guild/perks', region, **filters)

    def get_guild_achievements(self, region, **filters):
        """
        Data Resources API - all guild achievements
        """
        return self.get_resource('wow/data/guild/achievements', region, **filters)

    def get_item_classes(self, region, **filters):
        """
        Data Resources API - all item classes
        """
        return self.get_resource('wow/data/item/classes', region, **filters)

    def get_talents(self, region, **filters):
        """
        Data Resources API - all talents, specs and glyphs for each class
        """
        return self.get_resource('wow/data/talents', region, **filters)

    def get_pet_types(self, region, **filters):
        """
        Data Resources API - all pet types
        """
        return self.get_resource('wow/data/pet/types', region, **filters)

    # ---------------------------------------------------------------------------------------------
    # Game Data API wrappers
    # ---------------------------------------------------------------------------------------------

    # Achievement API

    def get_achievement_category_index(self, region, namespace, **filters):
        """
        Data Achievement API - Returns an index of achievement categories
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/achievement-category/index', region, **filters)

    def get_achievement_category(self, region, namespace, id, **filters):
        """
        Data Achievement API - Returns an achievement category by id
        """
        filters['namespace'] = namespace
        return self.get_resource('/data/wow/achievement-category/{0}', region, *[id],  **filters)

    def get_achievement_index(self, region, namespace, **filters):
        """
        Data Achievement API - Returns an index of achievements
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/achievement/index', region, **filters)

    def get_achievement_data(self, region, namespace, id, **filters):
        """
        Data Achievement API - Returns an achievement by id
        """
        filters['namespace'] = namespace
        return self.get_resource('/data/wow/achievement/{0}', region, *[id], **filters)

    def get_achievement_media(self, region, namespace, id, **filters):
        """
        Data Achievement API - Returns media for an achievement by id
        """
        filters['namespace'] = namespace
        return self.get_resource('/data/wow/media/achievement/{0}', region, *[id], **filters)

    # Azerite Essence API

    def get_azerite_essence_index(self, region, namespace, **filters):
        """
        Data Azerite Essence API - Returns an index of azerite essences
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/azerite-essence/index', region, **filters)

    def get_azerite_essence(self, region, namespace, id, **filters):
        """
        Data Azerite Essence API - Returns an azerite essence by id
        """
        filters['namespace'] = namespace
        return self.get_resource('/data/wow/azerite-essence/{0}', region, *[id], **filters)

    def get_azerite_essence_media(self, region, namespace, id, **filters):
        """
        Data Azerite Essence API - Returns media for an azerite essence by id
        """
        filters['namespace'] = namespace
        return self.get_resource('/data/wow/media/azerite-essence/{0}', region, *[id], **filters)

    # Connected Realm API

    def get_connected_realms(self, region, namespace, **filters):
        """
        Data Connected Realm API - Returns an index of connected realms
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/connected-realm/index', region, **filters)

    def get_connected_realm(self, region, namespace, id, **filters):
        """
        Data Connected Realm API - Returns a connected realm by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/connected-realm/{0}', region, *[id], **filters)

    # Creature API

    def get_creature_family_index(self, region, namespace, **filters):
        """
        Data Creature API - Returns an index of creature families
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/creature-family/index', region, **filters)

    def get_creature_family(self, region, namespace, id, **filters):
        """
        Data Creature API - Returns a creature family by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/creature-family/{0}', region, *[id], **filters)

    def get_creature_type_index(self, region, namespace, **filters):
        """
        Data Creature API - Returns an index of creature types
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/creature-type/index', region, **filters)

    def get_creature_type(self, region, namespace, id, **filters):
        """
        Data Creature API - Returns a creature type by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/creature-type/{0}', region, *[id], **filters)

    def get_creature(self, region, namespace, id, **filters):
        """
        Data Creature API - Returns a creature by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/creature/{0}', region, *[id], **filters)

    def get_creature_display_media(self, region, namespace, id, **filters):
        """
        Data Creature API - Returns media for a creature display by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/media/creature-display/{0}', region, *[id], **filters)

    def get_creature_family_media(self, region, namespace, id, **filters):
        """
        Data Creature API - Returns media for a creature family by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/media/creature-family/{0}', region, *[id], **filters)

    # Guild API

    def get_guild_data(self, region, namespace, realm_slug, guild_slug, **filters):
        """
        Data Guild API - Returns a single guild by its name and realm
        """
        filters['namespace'] = namespace
        params = [realm_slug, guild_slug]
        return self.get_resource('data/wow/guild/{0}/{1}', region, *params, **filters)

    def get_guild_achievements_data(self, region, namespace, realm_slug, guild_slug, **filters):
        """
        Data Guild API - Returns a single guild's achievements by name and realm
        """
        filters['namespace'] = namespace
        params = [realm_slug, guild_slug]
        return self.get_resource('data/wow/guild/{0}/{1}/achievements', region, *params, **filters)

    def get_guild_roster_data(self, region, namespace, realm_slug, guild_slug, **filters):
        """
        Data Guild API - Returns a single guild's roster by its name and realm
        """
        filters['namespace'] = namespace
        params = [realm_slug, guild_slug]
        return self.get_resource('data/wow/guild/{0}/{1}/roster', region, *params, **filters)

    # Guild Crest API

    def get_guild_crest_index(self, region, namespace, **filters):
        """
        Guild Crest API - Returns an index of guild crest media
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/guild-crest/index', region, **filters)

    def get_guild_crest_border_media(self, region, namespace, id, **filters):
        """
        Guild Crest API - Returns media for a guild crest border by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/media/guild-crest/border/{0}', region, *[id], **filters)

    def get_guild_crest_emblem_media(self, region, namespace, id, **filters):
        """
        Guild Crest API - Returns media for a guild crest emblem by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/media/guild-crest/emblem/{0}', region, *[id], **filters)

    # Item API

    def get_item_class_index(self, region, namespace, **filters):
        """
        Item API - Returns an index of item classes
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/item-class/index', region, **filters)

    def get_item_class(self, region, namespace, id, **filters):
        """
        Item API - Returns an item class by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/item-class/{0}', region, *[id], **filters)

    def get_item_subclass(self, region, namespace, class_id, subclass_id, **filters):
        """
        Item API - Returns an item subclass by id
        """
        filters['namespace'] = namespace
        params = [class_id, subclass_id]
        resource = 'data/wow/item-class/{0}/item-subclass/{1}'
        return self.get_resource(resource, region, *params, **filters)

    def get_item_data(self, region, namespace, id, **filters):
        """
        Item API - Returns an item by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/item/{0}', region, *[id], **filters)

    def get_item_media(self, region, namespace, id, **filters):
        """
        Item API - Returns media for an item by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/media/item/{0}', region, *[id], **filters)

    # Mythic Keystone Affix API

    def get_mythic_keystone_affixes(self, region, namespace, **filters):
        """
        Mythic Keystone Affix API - get mythic keystone affixes
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/keystone-affix/index', region, **filters)

    def get_mythic_keystone_affix(self, region, namespace, affix_id, **filters):
        """
        Mythic Keystone Affix API - get mythic keystone affix by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/keystone-affix/{0}', region, *[affix_id], **filters)

    # Mythic Raid Leaderboard API

    def get_mythic_raid_leaderboard(self, region, namespace, raid, faction, **filters):
        """
        Mythic Raid Leaderboard API - get mythic raid leaderboard of specific faction
        """
        filters['namespace'] = namespace
        return self.get_resource(
            'data/wow/leaderboard/hall-of-fame/{0}/{1}',
            region,
            *[raid, faction],
            **filters
        )

    # Mount API

    def get_mount_index(self, region, namespace, **filters):
        """
        Mount API - Returns an index of mounts
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/mount/index', region, **filters)

    def get_mount_data(self, region, namespace, id, **filters):
        """
        Mount API - Returns a mount by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/mount/{0}', region, *[id], **filters)

    # Mythic Keystone Dungeon API

    def get_mythic_keystone_dungeons(self, region, namespace, **filters):
        """
        Mythic Keystone Dungeon API - get all mythic keystone dungeons
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/mythic-keystone/dungeon/index', region, **filters)

    def get_mythic_keystone_dungeon(self, region, namespace, dungeon_id, **filters):
        """
        Mythic Keystone Dungeon API - get mythic keystone dungeon by id
        """
        filters['namespace'] = namespace
        return self.get_resource(
            'data/wow/mythic-keystone/dungeon/{0}', region, *[dungeon_id], **filters)

    def get_mythic_keystones(self, region, namespace, **filters):
        """
        Mythic Keystone Dungeon API - get links to documents related to mythic keystone dungeons
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/mythic-keystone/index', region, **filters)

    def get_mythic_keystone_periods(self, region, namespace, **filters):
        """
        Mythic Keystone Dungeon API - get all mythic keystone periods
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/mythic-keystone/period/index', region, **filters)

    def get_mythic_keystone_period(self, region, namespace, period_id, **filters):
        """
        Mythic Keystone Dungeon API - get mythic keystone period by id
        """
        filters['namespace'] = namespace
        return self.get_resource(
            'data/wow/mythic-keystone/period/{0}', region, *[period_id], **filters)

    def get_mythic_keystone_seasons(self, region, namespace, **filters):
        """
        Mythic Keystone Dungeon API - get all mythic keystone seasons
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/mythic-keystone/season/index', region, **filters)

    def get_mythic_keystone_season(self, region, namespace, season_id, **filters):
        """
        Mythic Keystone Dungeon API - get mythic keystone season by id
        """
        filters['namespace'] = namespace
        return self.get_resource(
            'data/wow/mythic-keystone/season/{0}', region, *[season_id], **filters)

    # Mythic Keystone Leaderboard API

    def get_mythic_keystone_leaderboards(self, region, namespace, connected_realm_id, **filters):
        """
        Mythic Keystone Leaderboard API
        Returns an index of Mythic Keystone Leaderboard dungeon instances for a connected realm
        """
        filters['namespace'] = namespace
        resource = 'data/wow/connected-realm/{0}/mythic-leaderboard/index'
        return self.get_resource(resource, region, *[connected_realm_id], **filters)

    def get_mythic_keystone_leaderboard(self,
                                        region, namespace, connected_realm_id, dungeon_id, period,
                                        **filters):
        """
        Mythic Keystone Leaderboard API - get a weekly mythic keystone leaderboard by period
        """
        filters['namespace'] = namespace
        resource = 'data/wow/connected-realm/{0}/mythic-leaderboard/{1}/period/{2}'
        params = [connected_realm_id, dungeon_id, period]
        return self.get_resource(resource, region, *params, **filters)

    # Pet API

    def get_pet_index(self, region, namespace, **filters):
        """
        Pet API - Returns an index of pets
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/pet/index', region, **filters)

    def get_pet_data(self, region, namespace, id, **filters):
        """
        Pet API - Returns a pet by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/pet/{0}', region, *[id], **filters)

    # Playable Class API

    def get_playable_classes(self, region, namespace, **filters):
        """
        Playable Class API - get available playable classes
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/playable-class/index', region, **filters)

    def get_playable_class(self, region, namespace, class_id, **filters):
        """
        Playable Class API - get playable classes by class id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/playable-class/{0}', region, *[class_id], **filters)

    def get_playable_class_pvp_talent_slots(self, region, namespace, class_id, **filters):
        """
        Playable Class API - get pvp talent slots for a playable class by id
        """
        filters['namespace'] = namespace
        return self.get_resource(
            'data/wow/playable-class/{0}/pvp-talent-slots', region, *[class_id], **filters)

    # Playable Race API

    def get_races(self, region, namespace, **filters):
        """
        Playable Race API - get races
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/race/index', region, **filters)

    def get_race(self, region, namespace, race_id, **filters):
        """
        Playable Race API - get race by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/race/{0}', region, *[race_id], **filters)

    # Playable Specialization API

    def get_playable_specializations(self, region, namespace, **filters):
        """
        Playable Specialization API - get playable specializations
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/playable-specialization/index', region, **filters)

    def get_playable_specialization(self, region, namespace, spec_id, **filters):
        """
        Playable Specialization API - get playable specialization by spec id
        """
        filters['namespace'] = namespace
        return self.get_resource(
            'data/wow/playable-specialization/{0}',
            region,
            *[spec_id],
            **filters
        )

    # Power Type API

    def get_power_types(self, region, namespace, **filters):
        """
        Power Type API - get power types
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/power-type/index', region, **filters)

    def get_power_type(self, region, namespace, power_type_id, **filters):
        """
        Power Type API - get power type by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/power-type/{0}', region, *[power_type_id], **filters)

    # PvP Season API

    def get_pvp_season_index(self, region, namespace, **filters):
        """
        PvP Season API - Returns an index of PvP seasons
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/pvp-season/index', region, **filters)

    def get_pvp_season(self, region, namespace, season_id, **filters):
        """
        PvP Season API - Returns a PvP season by ID
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/pvp-season/{0}', region, *[season_id], **filters)

    def get_pvp_leaderboard_index(self, region, namespace, season_id, **filters):
        """
        PvP Season API - Returns an index of PvP leaderboards for a PvP season
        """
        filters['namespace'] = namespace
        resource = 'data/wow/pvp-season/{0}/pvp-leaderboard/index'
        return self.get_resource(resource, region, *[season_id], **filters)

    def get_pvp_leaderboard(self, region, namespace, season_id, bracket, **filters):
        """
        PvP Season API - Returns the PvP leaderboard of a specific PvP bracket for a PvP season
        """
        filters['namespace'] = namespace
        resource = 'data/wow/pvp-season/{0}/pvp-leaderboard/{1}'
        return self.get_resource(resource, region, *[season_id, bracket], **filters)

    def get_pvp_rewards_index(self, region, namespace, season_id, **filters):
        """
        PvP Season API - Returns an index of PvP rewards for a PvP season
        """
        filters['namespace'] = namespace
        resource = 'data/wow/pvp-season/{0}/pvp-reward/index'
        return self.get_resource(resource, region, *[season_id], **filters)

    # PvP Tier API

    def get_pvp_tier_media(self, region, namespace, tier_id, **filters):
        """
        PvP Tier API - Returns media for a PvP tier by ID
        """
        filters['namespace'] = namespace
        resource = 'data/wow/media/pvp-tier/{0}'
        return self.get_resource(resource, region, *[tier_id], **filters)

    def get_pvp_tier_index(self, region, namespace, **filters):
        """
        PvP Tier API - Returns an index of PvP tiers
        """
        filters['namespace'] = namespace
        resource = 'data/wow/pvp-tier/index'
        return self.get_resource(resource, region, **filters)

    def get_pvp_tier(self, region, namespace, tier_id, **filters):
        """
        PvP Tier API - Returns a PvP tier by ID
        """
        filters['namespace'] = namespace
        resource = 'data/wow/pvp-tier/{0}'
        return self.get_resource(resource, region, *[tier_id], **filters)

    # Realm API

    def get_realms(self, region, namespace, **filters):
        """
        Realm API - get realms
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/realm/index', region, **filters)

    def get_realm(self, region, namespace, realm_slug, **filters):
        """
        Realm API - get realm by realm slug
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/realm/{0}', region, *[realm_slug], **filters)

    # Region API

    def get_regions(self, region, namespace, **filters):
        """
        Region API - get regions
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/region/index', region, **filters)

    def get_region(self, region, namespace, region_id, **filters):
        """
        Region API - get region by region id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/region/{0}', region, *[region_id], **filters)

    # Title API

    def get_title_index(self, region, namespace, **filters):
        """
        Title API - Returns an index of titles
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/title/index', region, **filters)

    def get_title(self, region, namespace, id, **filters):
        """
        Title API - Returns a title by id
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/title/{0}', region, *[id], **filters)

    # WoW Token API

    def get_token(self, region, namespace, **filters):
        """
        WoW Token API - Returns the WoW Token index
        """
        filters['namespace'] = namespace
        return self.get_resource('data/wow/token/index', region, **filters)

    # ---------------------------------------------------------------------------------------------
    # Profile API wrappers
    # ---------------------------------------------------------------------------------------------

    # Character Achievements API

    def get_character_achievements_summary(self,
                                           region, realm_slug, character_name, namespace,
                                           **filters):
        """
        Character Achievements API
        Returns a summary of the achievements a character has completed
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}/achievements'
        return self.get_resource(resource, region, *[realm_slug, character_name], **filters)

    # Character Appearance API

    def get_character_appearance_summary(self,
                                         region, realm_slug, character_name, namespace,
                                         **filters):
        """
        Character Appearance API - Returns a summary of a character's appearance settings
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}/appearance'
        return self.get_resource(resource, region, *[realm_slug, character_name], **filters)

    # Character Equipment API

    def get_character_equipment_summary(self,
                                        region, realm_slug, character_name, namespace,
                                        **filters):
        """
        Character Equipment API - Returns a summary of the items equipped by a character
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}/equipment'
        return self.get_resource(resource, region, *[realm_slug, character_name], **filters)

    # Character Media API

    def get_character_media_summary(self,
                                    region, realm_slug, character_name, namespace,
                                    **filters):
        """
        Character Media API - Returns a summary of the media assets available for a character
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}/character-media'
        return self.get_resource(resource, region, *[realm_slug, character_name], **filters)

    # Character Profile API

    def get_character_profile_summary(self,
                                      region, realm_slug, character_name, namespace,
                                      **filters):
        """
        Character Profile API - Returns a profile summary for a character
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}'
        return self.get_resource(resource, region, *[realm_slug, character_name], **filters)

    # Character PvP API

    def get_character_pvp_bracket_stats(self,
                                        region, realm_slug, character_name, bracket, namespace,
                                        **filters):
        """
        Character PvP API - Returns the PvP bracket statistics for a character
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}/pvp-bracket/{2}'
        params = [realm_slug, character_name, bracket]
        return self.get_resource(resource, region, *params, **filters)

    def get_character_pvp_stats(self,
                                region, realm_slug, character_name, namespace,
                                **filters):
        """
        Character PvP API - Returns a PvP summary for a character
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}/pvp-summary'
        params = [realm_slug, character_name]
        return self.get_resource(resource, region, *params, **filters)

    # Character Specializations API

    def get_character_specializations_summary(self,
                                              region, realm_slug, character_name, namespace,
                                              **filters):
        """
        Character Specializations API - Returns a summary of a character's specializations
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}/specializations'
        params = [realm_slug, character_name]
        return self.get_resource(resource, region, *params, **filters)

    # Character Statistics API

    def get_character_stats_summary(self,
                                    region, realm_slug, character_name, namespace,
                                    **filters):
        """
        Character Statistics API - Returns a statistics summary for a character
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}/statistics'
        params = [realm_slug, character_name]
        return self.get_resource(resource, region, *params, **filters)

    # Character Titles API

    def get_character_titles_summary(self,
                                    region, realm_slug, character_name, namespace,
                                    **filters):
        """
        Character Titles API - Returns a summary of titles a character has obtained
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}/titles'
        params = [realm_slug, character_name]
        return self.get_resource(resource, region, *params, **filters)

    # WoW Mythic Keystone Character Profile API

    def get_character_mythic_keystone_profile(self,
                                              region, realm_slug, character_name, namespace,
                                              **filters):
        """
        Profile API - Mythic Keystone Character Profile Index
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}/mythic-keystone-profile'
        return self.get_resource(resource, region, *[realm_slug, character_name], **filters)

    def get_character_mythic_keystone_profile_season(self,
                                                     region, realm_slug, character_name, namespace,
                                                     season_id,
                                                     **filters):
        """
        Profile API - Returns the Mythic Keystone season details for a character
        """
        filters['namespace'] = namespace
        resource = 'profile/wow/character/{0}/{1}/mythic-keystone-profile/season/{2}'
        params = [realm_slug, character_name, season_id]
        return self.get_resource(resource, region, *params, **filters)
