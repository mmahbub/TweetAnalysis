import abc
import base64
import dbm
import time
import pickle
import re

from lxml.html import parse
from lxml.etree import tostring
from geopy.geocoders import Nominatim
from geopy.exc import GeopyError

from sync import RWLock

def dumps(x):
    return base64.b64encode(pickle.dumps(x))

def loads(x):
    return pickle.loads(base64.b64decode(x))

class DB(abc.ABC):
    def __init__(self, filename):
        super().__init__()
        self.db = dbm.open(filename, "c")
        self.cache = {}
        self.rwlock = RWLock()

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        self.close()

    def __contains__(self, key):
        try:
            self.rwlock.acquire_read()

            return key in self.cache or DB.from_db(key) in self.db
        finally:
            self.rwlock.release()

    def __getitem__(self, key):
        try:
            self.rwlock.acquire_read()

            if key not in self.cache:
                self.rwlock.promote()

                dbkey = dumps(key)

                if dbkey in self.db:
                    self.cache[key] = loads(self.db[dbkey])
                else:
                    self.cache[key] = self.refresh(key)

            return self.cache[key]
        finally:
            self.rwlock.release()

    def __setitem__(self, key, value):
        try:
            self.rwlock.acquire_write()

            self.cache[key] = value
        finally:
            self.rwlock.release()

    def __delitem__(self, key):
        try:
            self.rwlock.acquire_write()

            del self.cache[key]
            del self.db[dumps(key)]
        finally:
            self.rwlock.release()

    def sync(self):
        try:
            self.rwlock.acquire_write()

            # Flush cache to db
            for k, v in self.cache.items():
                self.db[dumps(k)] = dumps(v)

            try:
                self.db.sync()
            except AttributeError:
                pass
        finally:
            self.rwlock.release()

    def close(self):
        try:
            self.rwlock.acquire_write()

            # Flush cache to db
            for k, v in self.cache.items():
                self.db[dumps(k)] = dumps(v)

            self.db.close()
        finally:
            self.rwlock.release()

    @abc.abstractmethod
    def refresh(self, key):
        pass


REGEX_TAGS = re.compile(r'\<.+?\>')
REGEX_SQMI = re.compile(r'(?P<sqmi>[0-9\.,]+)\s*square miles')

class CityAreaDB(DB):
    def refresh(self, key):
        city, state = key

        if city is not None:
            html_url = "http://www.city-data.com/city/%s-%s.html" % key
            html_tag = '//section[@id="population-density"]'
        else:

            html_url = "http://www.city-data.com/city/%s.html" % state
            html_tag = '//span[@class="badge"]'

        try:
            html = parse(html_url)
        except OSError:
            return None

        time.sleep(1)

        for tag in html.xpath(html_tag):
            text = REGEX_TAGS.sub('', tostring(tag).decode())
            match = REGEX_SQMI.search(text)

            if match is not None:
                # Convert to km^2
                return float(match.group("sqmi").replace(',', '')) * 2.589988

        return None

class GeolocationDB(DB):
    def __init__(self, filename):
        super().__init__(filename)

        self.backend = Nominatim(
            user_agent = "curent-utk",
            country_bias = "USA"
        )

    def refresh(self, key):
        query = {"country": "USA"}

        if key.house_number is not None and key.street is not None:
            query["street"] = key.house_number + " " + key.street

        if key.city is not None:
            query["city"] = key.city

        if key.state is not None:
            query["state"] = key.state

        if key.zip_code is not None:
            query["postalcode"] = key.zip_code

        try:
            coord = self.backend.geocode(query)
        except GeopyError:
            return None

        # Nominatim allows at most one request per second
        # <https://operations.osmfoundation.org/policies/nominatim/>
        time.sleep(1)

        return None if coord is None else (coord.latitude, coord.longitude)
