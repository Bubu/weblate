# -*- coding: utf-8 -*-
#
# Copyright © 2012 - 2018 Michal Čihař <michal@cihar.com>
#
# This file is part of Weblate <https://weblate.org/>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

from __future__ import print_function
from unittest import SkipTest
import math
import time
import tempfile
import os
import json
from contextlib import contextmanager
from base64 import b64encode
from six.moves.http_client import HTTPConnection
import django
from django.conf import settings
from django.test.utils import override_settings
from django.urls import reverse
from django.core import mail

from PIL import Image

try:
    from selenium import webdriver
    from selenium.common.exceptions import (
        WebDriverException, ElementNotVisibleException,
        NoSuchElementException,
    )
    from selenium.webdriver.remote.file_detector import UselessFileDetector
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait, Select
    from selenium.webdriver.support.expected_conditions import staleness_of
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

import six

from weblate.lang.models import Language
from weblate.trans.tests.test_views import RegistrationTestMixin
from weblate.trans.tests.test_models import BaseLiveServerTestCase
from weblate.trans.tests.utils import create_test_user
from weblate.vcs.ssh import get_key_data

# Check whether we should run Selenium tests
DO_SELENIUM = (
    'DO_SELENIUM' in os.environ and
    'SAUCE_USERNAME' in os.environ and
    'SAUCE_ACCESS_KEY' in os.environ and
    HAS_SELENIUM
)


class SeleniumTests(BaseLiveServerTestCase, RegistrationTestMixin):
    caps = {
        'browserName': 'firefox',
        'platform': 'Windows 10',
    }
    driver = None
    image_path = None

    def set_test_status(self, passed=True):
        connection = HTTPConnection("saucelabs.com")
        connection.request(
            'PUT',
            '/rest/v1/{0}/jobs/{1}'.format(
                self.username, self.driver.session_id
            ),
            json.dumps({"passed": passed}),
            headers={"Authorization": "Basic {0}".format(self.sauce_auth)}
        )
        result = connection.getresponse()
        return result.status == 200

    def run(self, result=None):
        if result is None:
            result = self.defaultTestResult()

        errors = len(result.errors)
        failures = len(result.failures)
        super(SeleniumTests, self).run(result)

        if DO_SELENIUM:
            self.set_test_status(
                errors == len(result.errors) and
                failures == len(result.failures)
            )

    @contextmanager
    def wait_for_page_load(self, timeout=30):
        old_page = self.driver.find_element_by_tag_name('html')
        yield
        WebDriverWait(self.driver, timeout).until(
            staleness_of(old_page)
        )

    @classmethod
    def setUpClass(cls):
        if DO_SELENIUM:
            cls.caps['name'] = 'Weblate CI build'
            cls.caps['screenResolution'] = '1280x1024'
            # Fill in Travis details in caps
            if 'TRAVIS_JOB_NUMBER' in os.environ:
                cls.caps['tunnel-identifier'] = os.environ['TRAVIS_JOB_NUMBER']
                cls.caps['build'] = os.environ['TRAVIS_BUILD_NUMBER']
                cls.caps['tags'] = [
                    'python-{0}'.format(os.environ['TRAVIS_PYTHON_VERSION']),
                    'django-{0}'.format(django.get_version()),
                    'CI'
                ]

            # Use Sauce connect
            cls.username = os.environ['SAUCE_USERNAME']
            cls.key = os.environ['SAUCE_ACCESS_KEY']
            cls.sauce_auth = b64encode(
                '{}:{}'.format(cls.username, cls.key).encode('utf-8')
            )
            # We do not want to use file detector as it magically uploads
            # anything what matches local filename
            cls.driver = webdriver.Remote(
                desired_capabilities=cls.caps,
                command_executor="http://{0}:{1}@{2}/wd/hub".format(
                    cls.username,
                    cls.key,
                    'ondemand.saucelabs.com',
                ),
                file_detector=UselessFileDetector(),
            )
            cls.driver.implicitly_wait(10)
            cls.actions = webdriver.ActionChains(cls.driver)
            jobid = cls.driver.session_id
            print(
                'Sauce Labs job: https://saucelabs.com/jobs/{0}'.format(jobid)
            )
            cls.image_path = os.path.join(settings.BASE_DIR, 'test-images')
            if not os.path.exists(cls.image_path):
                os.makedirs(cls.image_path)
        super(SeleniumTests, cls).setUpClass()

    def setUp(self):
        if self.driver is None:
            raise SkipTest('Selenium Tests disabled')
        super(SeleniumTests, self).setUp()
        self.driver.get('{0}{1}'.format(self.live_server_url, reverse('home')))
        self.driver.set_window_size(1280, 1024)
        time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        super(SeleniumTests, cls).tearDownClass()
        if cls.driver is not None:
            cls.driver.quit()
            cls.driver = None

    def scroll_top(self):
        self.driver.execute_script('window.scrollTo(0, 0)')

    def screenshot(self, name):
        """Captures named full page screenshot."""
        self.scroll_top()
        # Get window and document dimensions
        window_height = self.driver.execute_script(
            'return window.innerHeight'
        )
        scroll_height = self.driver.execute_script(
            'return document.body.scrollHeight'
        )
        # Calculate number of screnshots
        num = int(math.ceil(float(scroll_height) / float(window_height)))

        # Create temporary files
        tempfiles = []
        for i in range(num):
            handle, path = tempfile.mkstemp(
                prefix='wl-shot-{0:02}-'.format(i), suffix='.png'
            )
            os.close(handle)
            tempfiles.append(path)

        try:
            # take screenshots
            for i, path in enumerate(tempfiles):
                if i > 0:
                    self.driver.execute_script(
                        'window.scrollBy(%d,%d)' % (0, window_height)
                    )

                self.driver.save_screenshot(path)

            # Stitch images together
            stiched = None
            for i, path in enumerate(tempfiles):
                img = Image.open(path)

                width, height = img.size
                offset = i * window_height

                if stiched is None:
                    stiched = Image.new('RGB', (width, scroll_height))

                # Remove overlapping area from last screenshot
                if i == (len(tempfiles) - 1) and i > 0:
                    crop_height = scroll_height % height
                    if crop_height:
                        img = img.crop(
                            (0, height - crop_height, width, height)
                        )
                        width, height = img.size

                stiched.paste(img, (0, offset))

            stiched.save(os.path.join(self.image_path, name))
        finally:
            # Temp files cleanup
            for path in tempfiles:
                if os.path.isfile(path):
                    os.remove(path)
        self.scroll_top()

    def click(self, element):
        """Wrapper to scroll into element for click"""
        if isinstance(element, six.string_types):
            element = self.driver.find_element_by_link_text(element)

        try:
            element.click()
        except ElementNotVisibleException:
            self.actions.move_to_element(element).perform()
            element.click()

    def clear_field(self, element):
        element.send_keys(Keys.CONTROL + 'a')
        element.send_keys(Keys.DELETE)
        return element

    def do_login(self, create=True, superuser=False):
        # login page
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_id('login-button'),
            )

        # Create user
        if create:
            user = create_test_user()
            if superuser:
                user.is_superuser = True
                user.save()
            user.profile.langauge = 'en'
            user.profile.save()
            user.profile.languages.set(
                Language.objects.filter(code__in=('he', 'cs', 'hu'))
            )
        else:
            user = None

        # Login
        username_input = self.driver.find_element_by_id('id_username')
        username_input.send_keys('weblate@example.org')
        password_input = self.driver.find_element_by_id('id_password')
        password_input.send_keys('testpassword')

        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_xpath('//input[@value="Login"]')
            )
        return user

    def open_admin(self):
        # Login as superuser
        user = self.do_login(superuser=True)

        # Open admin page
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_id('admin-button'),
            )
        return user

    def test_admin(self):
        self.do_login(superuser=True)
        self.screenshot('admin-wrench.png')

    def test_failed_login(self):
        self.do_login(create=False)

        # We should end up on login page as user was invalid
        self.driver.find_element_by_id('id_username')

    def test_login(self):
        # Do proper login with new user
        self.do_login()

        # Load profile
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_id('profile-button')
            )

        # Wait for profile to load
        self.driver.find_element_by_id('subscriptions')

        # Finally logout
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_id('logout-button')
            )

        # We should be back on home page
        self.driver.find_element_by_id('suggestions')

    def register_user(self):
        # registration page
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_id('register-button'),
            )

        # Fill in registration form
        self.driver.find_element_by_id(
            'id_email'
        ).send_keys(
            'weblate@example.org'
        )
        self.driver.find_element_by_id(
            'id_username'
        ).send_keys(
            'test-example'
        )
        self.driver.find_element_by_id(
            'id_fullname'
        ).send_keys(
            'Test Example'
        )
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_xpath('//input[@value="Register"]')
            )

        # Wait for registration email
        loops = 0
        while not mail.outbox:
            time.sleep(1)
            loops += 1
            if loops > 20:
                break

        return ''.join(
            (self.live_server_url, self.assert_registration_mailbox())
        )

    @override_settings(REGISTRATION_CAPTCHA=False)
    def test_register(self, clear=False):
        """Test registration."""
        url = self.register_user()

        # Delete all cookies
        if clear:
            try:
                self.driver.delete_all_cookies()
            except WebDriverException as error:
                # This usually happens when browser fails to delete some
                # of the cookies for whatever reason.
                print('Ignoring: {0}'.format(error))

        # Confirm account
        self.driver.get(url)

        # Check we're logged in
        self.assertTrue(
            'Test Example' in
            self.driver.find_element_by_id('profile-button').text
        )

        # Check we got message
        self.assertTrue(
            'You have activated' in
            self.driver.find_element_by_tag_name('body').text
        )

    def test_register_nocookie(self):
        """Test registration without cookies."""
        self.test_register(True)

    def test_admin_ssh(self):
        """Test admin interface."""
        self.open_admin()

        self.screenshot('admin.png')

        # Open SSH page
        with self.wait_for_page_load():
            self.click('SSH keys')

        # Generate SSH key
        if get_key_data() is None:
            with self.wait_for_page_load():
                self.click(
                    self.driver.find_element_by_id('generate-ssh-button'),
                )

        # Add SSH host key
        self.driver.find_element_by_id(
            'ssh-host'
        ).send_keys(
            'github.com'
        )
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_id('ssh-add-button'),
            )

        self.screenshot('ssh-keys-added.png')


        # Open SSH page for final screenshot
        with self.wait_for_page_load():
            self.click('Home')
        with self.wait_for_page_load():
            self.click('SSH keys')
        self.screenshot('ssh-keys.png')

    def test_admin_componentlist(self):
        """Test admin interface."""
        self.open_admin()

        with self.wait_for_page_load():
            self.click('Component lists')

        with self.wait_for_page_load():
            self.click(self.driver.find_element_by_class_name('addlink'))
        self.driver.find_element_by_id('id_name').send_keys('All components')

        self.click('Add another Automatic component list assignment')
        self.clear_field(
            self.driver.find_element_by_id(
                'id_autocomponentlist_set-0-project_match'
            )
        ).send_keys('^.*$')
        self.clear_field(
            self.driver.find_element_by_id(
                'id_autocomponentlist_set-0-component_match'
            )
        ).send_keys('^.*$')
        self.screenshot('componentlist-add.png')

        with self.wait_for_page_load():
            self.driver.find_element_by_id('id_name').submit()

        # Ensure the component list is there
        self.click('All components')

    def test_weblate(self):
        user = self.open_admin()
        language_regex = '^(cs|he|hu)$'

        # Add project
        with self.wait_for_page_load():
            self.click('Projects')
        with self.wait_for_page_load():
            self.click(self.driver.find_element_by_class_name('addlink'))
        self.driver.find_element_by_id('id_name').send_keys('WeblateOrg')
        Select(
            self.driver.find_element_by_id('id_access_control')
        ).select_by_value('1')
        self.driver.find_element_by_id(
            'id_web'
        ).send_keys(
            'https://weblate.org/'
        )
        self.driver.find_element_by_id(
            'id_mail'
        ).send_keys(
            'weblate@lists.cihar.com'
        )
        self.driver.find_element_by_id(
            'id_instructions'
        ).send_keys(
            'https://weblate.org/contribute/'
        )
        self.screenshot('add-project.png')
        with self.wait_for_page_load():
            self.driver.find_element_by_id('id_name').submit()

        # Add component
        with self.wait_for_page_load():
            self.click('Home')
        with self.wait_for_page_load():
            self.click('Components')
        with self.wait_for_page_load():
            self.click(self.driver.find_element_by_class_name('addlink'))

        self.driver.find_element_by_id('id_name').send_keys('Language names')
        Select(
            self.driver.find_element_by_id('id_project')
        ).select_by_visible_text('WeblateOrg')
        self.driver.find_element_by_id(
            'id_repo'
        ).send_keys(
            'https://github.com/WeblateOrg/demo.git'
        )
        self.driver.find_element_by_id(
            'id_repoweb'
        ).send_keys(
            'https://github.com/WeblateOrg/demo/blob/'
            '%(branch)s/%(file)s#L%(line)s'
        )
        self.driver.find_element_by_id(
            'id_filemask'
        ).send_keys(
            'weblate/langdata/locale/*/LC_MESSAGES/django.po'
        )
        self.driver.find_element_by_id(
            'id_new_base'
        ).send_keys(
            'weblate/langdata/locale/django.pot'
        )
        Select(
            self.driver.find_element_by_id('id_file_format')
        ).select_by_value('po')
        self.driver.find_element_by_id('id_license').send_keys('GPL-3.0+')
        self.driver.find_element_by_id(
            'id_license_url'
        ).send_keys(
            'https://spdx.org/licenses/GPL-3.0+'
        )
        self.clear_field(
            self.driver.find_element_by_id(
                'id_language_regex'
            )
        ).send_keys(language_regex)
        self.screenshot('add-component.png')
        # This takes long
        with self.wait_for_page_load(timeout=1200):
            self.driver.find_element_by_id('id_name').submit()
        with self.wait_for_page_load():
            self.click('Language names')

        # Load Weblate project page
        try:
            # Some browsers to apply CSS transformations when looking
            element = self.driver.find_element_by_link_text('View site')
        except NoSuchElementException:
            element = self.driver.find_element_by_link_text('VIEW SITE')
        with self.wait_for_page_load():
            self.click(element)
        self.click('Tools')
        with self.wait_for_page_load():
            self.click('All projects')
        with self.wait_for_page_load():
            self.click('WeblateOrg')

        # User management
        self.click('Manage')
        with self.wait_for_page_load():
            self.click('Manage users')
        element = self.driver.find_element_by_id('id_user')
        element.send_keys('testuser')
        with self.wait_for_page_load():
            element.submit()
        with self.wait_for_page_load():
            self.click('Manage users')
        self.screenshot('manage-users.png')
        self.screenshot('project-access.png')
        # The project is now watched
        self.click('Watched projects')
        with self.wait_for_page_load():
            self.click('WeblateOrg')

        # Engage page
        self.click('Share')
        with self.wait_for_page_load():
            self.click('Engage page')
        self.screenshot('engage.png')
        with self.wait_for_page_load():
            self.click('Translation project for WeblateOrg')

        # Glossary
        self.click('Glossaries')
        with self.wait_for_page_load():
            self.click('Manage all glossaries')
        with self.wait_for_page_load():
            self.click('Czech')
        self.click('Add new word')
        self.driver.find_element_by_id('id_source').send_keys('language')
        element = self.driver.find_element_by_id('id_target')
        element.send_keys('jazyk')
        with self.wait_for_page_load():
            element.submit()
        self.screenshot('glossary-edit.png')
        self.click('Watched projects')
        with self.wait_for_page_load():
            self.click('WeblateOrg')
        self.click('Glossaries')
        self.screenshot('project-glossaries.png')

        # Addons
        self.click('Components')
        with self.wait_for_page_load():
            self.click('Language names')
        self.click('Manage')
        with self.wait_for_page_load():
            self.click('Addons')
        self.screenshot('addons.png')
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_xpath(
                    '//button[@data-addon="weblate.discovery.discovery"]'
                )
            )
        element = self.driver.find_element_by_id('id_match')
        element.send_keys(
            'weblate/locale/(?P<language>[^/]*)/LC_MESSAGES/'
            '(?P<component>[^/]*)\\.po'
        )
        self.clear_field(
            self.driver.find_element_by_id(
                'id_language_regex'
            )
        ).send_keys(language_regex)
        self.driver.find_element_by_id(
            'id_new_base_template'
        ).send_keys(
            'weblate/locale/{{ component }}.pot'
        )
        self.clear_field(
            self.driver.find_element_by_id('id_name_template')
        ).send_keys(
            '{{ component|title }}'
        )
        Select(
            self.driver.find_element_by_id('id_file_format')
        ).select_by_value('po')
        with self.wait_for_page_load():
            element.submit()
        self.screenshot('addon-discovery.png')
        element = self.driver.find_element_by_id('id_confirm')
        self.click(element)
        # This takes long
        with self.wait_for_page_load(timeout=1200):
            element.submit()
        with self.wait_for_page_load():
            self.click('Language names')

        # Reports
        self.click('Insights')
        self.click('Translation reports')
        self.click('Insights')
        self.screenshot('reporting.png')

        # Contributor agreeement
        self.click('Manage')
        with self.wait_for_page_load():
            self.click('Settings')
        element = self.driver.find_element_by_id('id_agreement')
        element.send_keys('This is an agreement.')
        with self.wait_for_page_load():
            element.submit()
        with self.wait_for_page_load():
            self.click('Language names')
        self.screenshot('contributor-agreement.png')
        with self.wait_for_page_load():
            self.click('View contributor agreement')
        element = self.driver.find_element_by_id('id_confirm')
        self.click(element)
        with self.wait_for_page_load():
            element.submit()

        # Translation page
        with self.wait_for_page_load():
            self.click('Czech')
        with self.wait_for_page_load():
            self.click('Django')
        self.screenshot('strings-to-check.png')
        self.click('Files')
        self.click('Upload translation')
        self.click('Files')
        self.screenshot('export-import.png')
        self.click('Tools')
        self.click('Automatic translation')
        self.click(
            self.driver.find_element_by_id('id_select_auto_source_2')
        )
        self.click('Tools')
        self.screenshot('automatic-translation.png')
        self.click('Search')
        element = self.driver.find_element_by_id('id_q')
        element.send_keys('%(count)s word')
        Select(
            self.driver.find_element_by_id('id_search')
        ).select_by_value('substring')
        with self.wait_for_page_load():
            element.submit()
        self.click('History')
        self.screenshot('format-highlight.png')
        self.click('Comments')
        self.screenshot('plurals.png')

        # Secondary language display
        user.profile.secondary_languages.set(
            Language.objects.filter(code__in=('he',))
        )
        with self.wait_for_page_load():
            self.click('Czech')
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_partial_link_text('All strings')
            )
        self.click('Other languages')
        self.screenshot('secondary-language.png')

        # RTL translation
        with self.wait_for_page_load():
            self.click('Django')
        with self.wait_for_page_load():
            self.click('Hebrew')
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_partial_link_text('All strings')
            )
        self.screenshot('visual-keyboard.png')

        # Source review
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_id('edit-screenshot')
            )
        self.screenshot('source-review-detail.png')
        self.screenshot('source-review-edit.png')
        with self.wait_for_page_load():
            self.click('source strings')
        self.screenshot('source-review.png')

        # Profile
        with self.wait_for_page_load():
            self.click(
                self.driver.find_element_by_id('profile-button')
            )
        self.click('Preferences')
        self.click(
            self.driver.find_element_by_id('id_dashboard_view')
        )
        self.screenshot('dashboard-dropdown.png')
        self.click('Subscriptions')
        self.screenshot('profile-subscriptions.png')
        self.click('Licenses')
        self.screenshot('profile-licenses.png')

        # Dashboard
        with self.wait_for_page_load():
            self.click('Dashboard')
        self.screenshot('your-translations.png')


# What other platforms we want to test
EXTRA_PLATFORMS = {
    'Chrome': {
        'browserName': 'chrome',
        'platform': 'Windows 10',
    },
}


def create_extra_classes():
    """Create classes for testing with other browsers"""
    classes = {}
    for platform, caps in EXTRA_PLATFORMS.items():
        name = '{0}_{1}'.format(
            platform,
            SeleniumTests.__name__,
        )
        classdict = dict(SeleniumTests.__dict__)
        classdict.update({
            'caps': caps,
        })
        classes[name] = type(name, (SeleniumTests,), classdict)

    globals().update(classes)


create_extra_classes()
