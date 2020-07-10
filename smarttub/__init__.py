import datetime
import logging
import sys
import time
from typing import List

import dateutil.parser
import jwt
import requests

logger = logging.getLogger(__name__)

class SmartTub:
    """Interface to the SmartTub API
    """

    AUTH_AUDIENCE = 'https://api.operation-link.com/'
    AUTH_URL = 'https://smarttub.auth0.com/oauth/token'
    AUTH_CLIENT_ID = 'dB7Rcp3rfKKh0vHw2uqkwOZmRb5WNjQC'
    AUTH_REALM = 'Username-Password-Authentication'
    AUTH_ACCOUNT_ID_KEY = 'http://operation-link.com/account_id'

    API_BASE = 'https://api.smarttub.io'
    API_KEY = 'TfXKgnYbv81lRdJBQcmGH6lWuA2V6oJp7xPlQRTz'

    def __init__(self):
        self.logged_in = False

    def login(self, username: str, password: str):
        """Authenticate to SmartTub

        This method must be called before any get_ or set_ methods

        username -- the email address for the SmartTub account
        password -- the password for the SmartTub account
        """

        # https://auth0.com/docs/api-auth/tutorials/password-grant
        r = requests.post(
            self.AUTH_URL,
            json={
                "audience": self.AUTH_AUDIENCE,
                "client_id": self.AUTH_CLIENT_ID,
                "grant_type": "http://auth0.com/oauth/grant-type/password-realm",
                "realm": self.AUTH_REALM,
                "scope": "openid email offline_access User Admin",
                "username": username,
                "password": password,
            }
        )
        r.raise_for_status()
        j = r.json()

        self.access_token = j['access_token']
        self.access_token_data = jwt.decode(self.access_token, verify=False)
        self.expires_at = time.time() + j['expires_in']
        self.refresh_token = j['refresh_token']
        assert j['token_type'] == 'Bearer'

        self.account_id = self.access_token_data[self.AUTH_ACCOUNT_ID_KEY]
        self.logged_in = True

        logger.debug(f'login successful, username={username}')

    @property
    def _headers(self):
        return {'Authorization': f'Bearer {self.access_token}'}

    def _require_login(self):
        if not self.logged_in:
            raise RuntimeError('not logged in')

    def request(self, method, path, body=None):
        self._require_login()
        r = requests.request(method, f'{self.API_BASE}/{path}', headers=self._headers, json=body)
        r.raise_for_status()
        return r.json()

    def get_account(self) -> 'Account':
        """Retrieve the SmartTub account of the authenticated user
        """

        self._require_login()
        r = requests.get(f'{self.API_BASE}/accounts/{self.account_id}', headers=self._headers)
        r.raise_for_status()
        j = r.json()
        account = Account(self, **j)
        logger.debug(f'get_account successful: {j}')
        return account


class Account:
    def __init__(self, _api: SmartTub, **properties):
        self._api = _api
        self.id = properties['id']
        self.email = properties['email']
        self.properties = properties

    def get_spas(self):
        spas = []
        for spa_info in self._api.request('GET', f'spas?ownerId={self.id}')['content']:
            spas.append(self.get_spa(spa_info['id']))
        return spas

    def get_spa(self, spa_id):
        return Spa(self._api, self, **self._api.request('GET', f'spas/{spa_id}'))


class Spa:
    SECONDARY_FILTRATION_MODES = {'FREQUENT', 'INFREQUENT', 'AWAY'}
    HEAT_MODES = {'ECONOMY', 'DAY', 'AUTO'}
    LIGHT_MODES = {'PURPLE', 'ORANGE', 'RED', 'YELLOW', 'GREEN', 'AQUA', 'BLUE', 'HIGH_SPEED_WHEEL', 'OFF'}
    TEMPERATURE_FORMATS = ['FAHRENHEIT', 'CELSIUS']
    ENERGY_USAGE_INTERVALS = ['DAY', 'MONTH']

    def __init__(self, _api: SmartTub, account: Account, **properties):
        self._api = _api
        self.account = account
        self.id = properties['id']
        self.brand = properties['brand']
        self.model = properties['model']
        self.properties = properties

    def request(self, method, resource: str, body=None):
        path = f'spas/{self.id}/{resource}'
        j = self._api.request(method, path, body)
        logger.debug(f'{method} {resource} successful: {j}')
        return j

    def get_status(self) -> dict:
        return self.request('GET', 'status')

    def get_pumps(self) -> list:
        pumps = []
        for pump_info in self.request('GET', 'pumps')['pumps']:
            pumps.append(SpaPump(self._api, self, **pump_info))
        return pumps

    def get_lights(self) -> list:
        lights = []
        for light_info in self.request('GET', 'lights')['lights']:
            lights.append(SpaLight(self._api, self, **light_info))
        return lights

    def get_errors(self) -> list:
        return self.request('GET', 'errors')['content']

    def get_reminders(self) -> dict:
        # API returns both 'reminders' and 'filters', seem to be identical
        reminders = []
        for reminder_info in self.request('GET', 'reminders')['reminders']:
            reminders.append(SpaReminder(self._api, self, **reminder_info))
        return reminders

    def get_debug_status(self) -> dict:
        return self.request('GET', 'debugStatus')['debugStatus']

    def get_energy_usage(self, interval: str, start_date: datetime.date, end_date: datetime.date) -> list:
        assert interval in self.ENERGY_USAGE_INTERVALS
        body = {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "interval": interval,
        }
        return self.request('POST', 'energyUsage', body)['buckets']

    def set_secondary_filtration_mode(self, mode: str):
        assert mode in self.SECONDARY_FILTRATION_MODES
        body = {
            'secondaryFiltrationConfig': mode
        }
        self.request('PATCH', 'config', body)

    def set_heat_mode(self, mode: str):
        assert mode in self.HEAT_MODES
        body = {
            'heatMode': mode
        }
        self.request('PATCH', 'config', body)

    def set_temperature(self, temp_c: float):
        body = {
            'setTemperature': temp_c
        }
        self.request('PATCH', 'config', body)

    def toggle_clearray(self, str):
        self.request('POST', 'clearray/toggle')

    def set_temperature_format(self, temperature_format: str):
        assert temperature_format in self.TEMPERATURE_MODES
        body = {
            'displayTemperatureFormat': temperature_format
        }
        self.request('POST', 'config', body)

    def set_date_time(self, date: datetime.date=None, time: datetime.time=None):
        """Set the spa date, time, or both
        """

        assert date is not None or time is not None
        config = {}
        if date is not None:
            config['date'] = date.isoformat()
        if time is not None:
            config['time'] = time.isoformat('minutes')
        body = {
            'dateTimeConfig': config
        }
        self.request('POST', body)

    def __str__(self):
        return f'<Spa {self.id}>'


class SpaPump:
    def __init__(self, _api: SmartTub, spa: Spa, **properties):
        self._api = _api
        self.spa = spa
        self.id = properties['id']
        self.speed = properties['speed']
        self.state = properties['state']
        self.type = properties['type']
        self.properties = properties

    def toggle(self):
        self.spa.request('POST', f'pumps/{self.id}/toggle')

    def __str__(self):
        return f'<SpaPump {self.id}>'


class SpaLight:
    def __init__(self, _api: SmartTub, spa: Spa, **properties):
        self._api = _api
        self.spa = spa
        self.zone = properties['zone']
        self.color = properties['color']
        self.intensity = properties['intensity']
        self.mode = properties['mode']
        self.properties = properties

    def set(self, intensity: int, mode: str):
        assert mode in self.LIGHT_MODES
        assert (intensity == 0) == (mode == 'OFF')
        body = {
            'intensity': intensity,
            'mode': mode,
        }
        self.spa.request('PATCH', f'lights/{self.zone}', body)


    def toggle(self):
        self.spa.request('POST', f'pumps/{self.id}/toggle')

    def __str__(self):
        return f'<SpaLight {self.zone}>'

class SpaReminder:
    def __init__(self, _api: SmartTub, spa: Spa, **properties):
        self._api = _api
        self.spa = spa
        self.id = properties['id']
        self.last_updated = dateutil.parser.isoparse(properties['lastUpdated'])
        self.name = properties['name']
        self.remaining_days = properties['remainingDuration']
        self.snoozed = properties['snoozed']
        self.state = properties['state']

    # TODO: snoozing

    def __str__(self):
        return f'<SpaReminder {self.id}>'
