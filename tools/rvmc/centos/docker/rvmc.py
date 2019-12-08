#!/usr/bin/python3
###############################################################################
#
# Copyright (c) 2019 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#

"""Redfish Virtual Media Controller"""

###############################################################################
#
# This Redfish Virtual Media Controller forces an install of a specified BMC's
# host using the Redfish Platform management protocol.
#
# To do so the following Redfish operations are performed
#
#     Step 1: Client Connect ... Establish a client connection to the BMC
#     Step 2: Root Query     ... Learn Redfish Services offered by the BMC
#     Step 3: Find CD/DVD    ... Locate the virtual media CD/DVD device
#     Step 4: Power Off Host ... Host power needs to be off
#     Step 5: Inject Iso     ... Inject the URL based ISO image into CD/DVD
#     Step 6: Force DVD Boot ... Set Net boot device to be CD/DVD
#     Step 7: Power On Host  ... Host will boot and install from DVD
#
# Note: All server starting state conditions such as the server running or
#       being stuck in POST, say at the grub prompt due to previous host boot
#       failure, the host needs to be in the powered off state for the ISO
#       Insertion and Set DVD for Next Boot steps.
#
# Calling Sequence: Single Server Config
#
#    > rvmc.py
#
#    Config file assumed to be '/etc/rvmc.yaml' with the following format
#
#    bmc_address: <bmc ip address>
#    bmc_username: <the bmc username>
#    bmc_password: <base64 encoded password>
#    image: http://<ip>:<port>/<path>/bootimage.iso
#
#    Real Example:
#
#    bmc_address: 123.45.67.89
#    bmc_username: root
#    bmc_password: TGk2OW51eCo=
#    image: http://[2620:10a:a001:a103::81]:8080/iso/sub_cloud_bootimage.iso
#
# Optional Arguments: Additional arguments inform the tool to look for a
#                     multi target config file and enable debug logs
#
#    --target <list of one or more comma separated targets>
#
#    Multi Server Config : Each BMC is represented by an arbitrary target name
#    -------------------   under a primary label called virtual_media_iso.
#
#    Config file assumed to be '/etc/rvmc.yaml' with the following format
#
#    virtual_media_iso:
#        dcloud1:
#            bmc_address: <bmc ip address>
#            bmc_username: <the bmc username>
#            bmc_password: <base64 encoded>
#            image: http://<ip>:<port>/<path>/bootimage.iso
#        dcloud2:
#            bmc_address: <bmc ip address>
#            bmc_username: <the bmc username>
#            bmc_password: <base64 encoded>
#            image: http://<ip>:<port>/<path>/bootimage.iso
#        dcloud3:
#            bmc_address: <bmc ip address>
#            bmc_username: <the bmc username>
#            bmc_password: <base64 encoded>
#            image: http://<ip>:<port>/<path>/bootimage.iso
#
#    > rvmc.py --target dcloud1,dcloud2,dcloud3 --debug level
#
#    --debug <0 .. 4>
#
#             Note: 0   no debug info
#                   1 = execution stage
#                   2 + http request logs
#                   3 + headers and payloads and misc other
#                   4 + json output of all command responses
#
#    > rvmc.py --target dcloud1,dcloud2,dcloud3 --debug <level>
#
###############################################################################
#
# Code structure: Note: any error causes error log, session close and exit
#
#   parse command line arguments
#
#   for each target
#       create object
#
#   for each object
#       execute(object)
#           _redfish_client_connect     ... connect to bmc
#           _redfish_root_query         ... get base url tree
#           _redfish_create_session     ... authenticated session
#           _redfish_get_managers       ... get managers urls
#           _redfish_get_systems_members .. get systems members info
#           _redfish_get_vm_url         ... get cd/dvd vm url
#           _redfish_load_vm_actions    ... get eject/insert action urls/info
#           _redfish_poweroff_host      ... tell bmc to power-off the host
#           _redfish_eject_image        ... eject current media if present
#           _redfish_insert_image       ... insert and verify insertion of iso
#           _redfish_set_boot_override  ... set boot from cd/dvd on next reset
#           _redfish_poweron_host       ... tell bmc to power-on the host
#
###############################################################################

import argparse
import base64
import datetime
import json
import os
import socket
import sys
import time
import yaml


# Import Redfish Python Library
# Module: https://pypi.org/project/redfish/
import redfish


FEATURE_NAME = 'Redfish Virtual Media Controller'
VERSION_MAJOR = 2
VERSION_MINOR = 0

POWER_ON = 'On'
POWER_OFF = "Off"

# The host system's mounted rvmc config file
CONFIG_FILE = '/etc/rvmc.yaml'

# Parse command line arguments
# ----------------------------
parser = argparse.ArgumentParser(description=FEATURE_NAME)

parser.add_argument("--target", type=str, required=False,
                    help="One or more bmc host descriptor targets ;\n"
                         "type: comma delimited target list")

parser.add_argument("--debug", type=int, required=False, default=0,
                    help="Optional debug level ; 1..4")

# get command line arguments
args = parser.parse_args()

# get debug level
debug = args.debug

# target list ; assumes none or comma delimited list
targets = []
if args.target and args.target != 'None':
    targets = args.target.split(',')


def t():
    """
    Return current time for log functions
    """

    return datetime.datetime.now().replace(microsecond=0)


def ilog(string, cr=True):
    """
    Info Log Utility
    """

    if cr is True or debug:
        sys.stdout.write("\n%s Info  : %s" % (t(), string))
    else:
        sys.stdout.write("%s, " % string)


def elog(string):
    """
    Error Log Utility
    """

    sys.stdout.write("\n%s Error : %s" % (t(), string))


def alog(string):
    """
    Action Log Utility
    """

    sys.stdout.write("\n%s Action: %s" % (t(), string))


def dlog1(string, level=1):
    """
    Debug Log - Level
    """

    if debug and level <= debug:
        sys.stdout.write("\n%s Debug%d: %s" % (t(), level, string))


def dlog2(string):
    """
    Debug Log - Level 2
    """

    dlog1(string, 2)


def dlog3(string):
    """
    Debug Log - Level 3
    """

    dlog1(string, 3)


def dlog4(string):
    """
    Debug Log - Level 4
    """

    dlog1(string, 4)


def slog(stage):
    """Execution Stage Log"""

    dlog1("Stage       : %s" % stage)


def rvmc_exit(code):
    """Exit not tied to object ; early fault handling"""

    sys.stdout.write("\n\n")
    sys.exit(code)


ilog("%s version %d.%d\n" % (FEATURE_NAME, VERSION_MAJOR, VERSION_MINOR))
dlog1("Debug       : %d" % debug)
if len(targets):
    dlog1("Targets     : %s" % (args.target))

# start with an empty object list
target_object_list = []

# Constants
# ---------
REDFISH_ROOT_PATH = '/redfish/v1'
PRIMARY_CONFIG_LABEL = 'virtual_media_iso'       # Primary Config label
SUPPORTED_VIRTUAL_MEDIA_DEVICES = ['CD', 'DVD']  # Maybe add USB to list

# headers for each request type
HDR_CONTENT_TYPE = "'Content-Type': 'application/json'"
HDR_ACCEPT = "'Accept': 'application/json'"

# they all happen to be the same right now
GET_HEADERS = {HDR_CONTENT_TYPE, HDR_ACCEPT}
POST_HEADERS = {HDR_CONTENT_TYPE, HDR_ACCEPT}
PATCH_HEADERS = {HDR_CONTENT_TYPE, HDR_ACCEPT}

# HTTP request types ; only 3 are required by this tool
POST = 'POST'
GET = 'GET'
PATCH = 'PATCH'


def is_ipv6_address(address):
    """
    Check IPv6 Address.

    :param address: the ip address to compare user name.
    :type address: str.
    :returns bool: True if address is an IPv6 address else False
    """

    try:
        socket.inet_pton(socket.AF_INET6, address)
        dlog3("Address    : %s is IPv6" % address)
    except socket.error:
        dlog3("Address    : %s is IPv4" % address)
        return False
    return True


def supported_device(devices):
    """
    Supported Device

    :param devices: list of devices
    :type : list
    :returns True if a device in devices list is in the
             SUPPORTED_VIRTUAL_MEDIA_DEVICES list.
             Otherwise False is returned.
    """

    for device in devices:
        if device in SUPPORTED_VIRTUAL_MEDIA_DEVICES:
            return True
    return False


def parse_target(target_name, target_dict):
    """
    Parse key value pairs in target and if successful create
    a vmcObject and add it to the target_object_list.

    :param target_name: arbitrary target label
    :type target_name: str.
    :param target_dict: dictionary of key value config file pairs
    :type target_dict: dictionary
    :returns nothing
    """

    dlog3("Parse Target: %s:%s" % (target_name, target_dict))

    pw = target_dict.get('bmc_password')
    if pw is None:
        elog("Failed get bmc password from config file")
        return

    try:
        pw_dec = base64.b64decode(pw).decode('utf-8')
    except Exception as ex:
        elog("Failed to decode bmc password found in config file (%s)" % ex)
        alog("Verify config file's bmc password is base64 encoded")
        return

    address = target_dict.get('bmc_address')
    if address is None:
        elog("Failed to decode bmc password found in %s" % CONFIG_FILE)
        alog("Verify config file's bmc password is base64 encoded")
        return

    ####################################################################
    #
    # Add url encoding [] for ipv6 addresses only.
    #
    # Note: The imported redfish library produces a python exception
    #       on the session close if the ipv4 address has [] around it.
    #
    #       I debugged the issue and know what it is and how to fix it
    #       but requires an upstream change that is not worth doing.
    #
    # URL Encoding for IPv6 only is an easy solution.
    #
    ######################################################################
    if is_ipv6_address(address) is True:
        address = '[' + address + ']'

    # Create object and add it to the target object list
    try:
        vmc_obj = VmcObject(target_name,
                            address,
                            target_dict.get('bmc_username'),
                            pw,
                            str(pw_dec),
                            target_dict.get('image'))
        if vmc_obj:
            target_object_list.append(vmc_obj)
        else:
            elog("Unable to create control object for target:%s ; "
                 "skipping ..." % target_dict)

    except Exception as ex:
        elog("Unable to parse configuration for '%s' (%s)"
             "in config file." % (target_dict, ex))
        alog("Check presence and spelling of configuration"
             " members under '%s' for target '%s'." %
             (PRIMARY_CONFIG_LABEL, target_dict))
    return


class VmcObject(object):
    """
    Virtual Media Controller Class Object. One for each BMC
    """

    def __init__(self,
                 hostname,
                 address,
                 username,
                 password,
                 password_decoded,
                 image):

        self.target = hostname
        self.uri = "https://" + address
        self.url = REDFISH_ROOT_PATH
        self.un = username.rstrip()
        self.ip = address.rstrip()
        self.pw_encoded = password.rstrip()
        self.pw = password_decoded
        self.img = image.rstrip()

        self.redfish_obj = None     # redfish client connection object
        self.session = False        # True when session for this BMC is created

        self.response = None        # holds response from last http request
        self.response_json = None   # json formatted version of above response
        self.response_dict = None   # dictionary version of aboe response

        # redfish root query response
        self.root_query_info = None  # json version of the full root query

        # Managers Info
        self.managers_group_url = None
        self.manager_members_list = []

        # Virtual Media Info
        self.vm_url = None
        self.vm_group_url = None
        self.vm_group = None
        self.vm_label = None
        self.vm_version = None
        self.vm_actions = {}
        self.vm_members_array = []
        self.vm_media_types = []

        # systems info
        self.systems_group_url = None
        self.systems_member_url = None
        self.systems_members_list = []
        self.systems_members = 0
        self.power_state = None

        # boot control info
        self.boot_control_dict = {}

        # systems reset info
        self.reset_command_url = None
        self.reset_action_dict = {}

        # parsed target object info
        if self.target is not None:
            dlog1("Target      : %s" % self.target)
        dlog1("BMC IP      : %s" % self.ip)
        dlog1("Username    : %s" % self.un)
        dlog1("Password    : %s" % self.pw_encoded)
        dlog1("Image       : %s" % self.img)

    def make_request(self, operation=None, path=None, payload=None):
        """
        Issue a Redfish http request,
        Check response,
        Convert response to dictionary format
        Convert response to json format

        :param operation: HTTP GET, POST or PATCH operation
        :type operation: str.
        :param path: url to perform request to
        :type path: str
        :param payload: POST or PATCH payload data
        :type payload: dictionary
        :returns True if request succeeded (200) otherwise False
        """

        self.response = None
        if path is not None:
            url = path
        else:
            url = self.url

        try:
            dlog3("Request     : %s %s" % (operation, url))
            if operation == GET:
                dlog3("Headers     : %s : %s" % (operation, GET_HEADERS))
                self.response = self.redfish_obj.get(url, headers=GET_HEADERS)

            elif operation == POST:
                dlog3("Headers     : %s : %s" % (operation, POST_HEADERS))
                dlog3("Payload     : %s" % payload)
                self.response = self.redfish_obj.post(url,
                                                      body=payload,
                                                      headers=POST_HEADERS)
            elif operation == PATCH:
                dlog3("Headers     : %s : %s" % (operation, PATCH_HEADERS))
                dlog3("Payload     : %s" % payload)
                self.response = self.redfish_obj.patch(url,
                                                       body=payload,
                                                       headers=POST_HEADERS)
            else:
                elog("Unsupported operation: %s" % operation)
                return False

        except Exception as ex:
            elog("Failed operation on '%s' (%s)" % (url, ex))

        if self.response is not None:

            # if we got a response, check its status
            if self.check_ok_status(url, operation) is False:
                self._exit(1)

            # format response
            try:
                if self.resp_dict() is True:
                    if self.format() is True:
                        dlog4("Response:\n%s\n" % self.response_json)
                        return True
                    else:
                        elog("Failed to parse BMC %s response '%s'" %
                             (operation, url))

            except Exception as ex:
                elog("Failed to parse BMC %s response '%s' (%s)" %
                     (operation, url, ex))

            elog("Response:\n%s\n" % self.response)
        else:
            elog("No response from %s:%s" % (operation, url))
        return False

    def resp_dict(self):
        """
        Create Response Dictionary
        """

        if self.response.read:
            self.response_dict = None
            try:
                self.response_dict = json.loads(self.response.read)
                return True
            except Exception as ex:
                elog("Got exception key valuing response ; (%s)" % ex)
                elog("Response: " % self.response.read)
        else:
            elog("No response from last command")
        return False

    def format(self):
        """
        Format Response as Json
        """

        self.response_json = None
        try:
            if self.resp_dict() is True:
                self.response_json = json.dumps(self.response_dict,
                                                indent=4,
                                                sort_keys=True)
                return True
            else:
                return False

        except Exception as ex:
            elog("Got exception formatting response ; (%s)\n" % ex)
            return False

    def get_key_value(self, key1, key2=None):
        """
        Get key1 value if no key2 is specified.
        Get key2 value from key1 value if key2 is specified.

        :param : key1 value is returned if no key2 is provided.
        :type : str.
        :param : key2 value is optional but if provided its value is returned
        :type : str
        :returns key1 value or key2 value if key2 is specified
        """

        value1 = self.response_dict.get(key1)
        if key2 is None:
            return value1
        return value1.get(key2)

    def check_ok_status(self, function, operation):
        """
        Status

        :param function: description of operation
        :type : str
        :param operation: http GET, POST or PATCH
        :type : str
        :returns True if response status is OK. Otherwise False.
        """

        if self.response.status not in [200, 202, 204]:

            elog("HTTP Status : %d ; %s %s failed\n%s\n" %
                 (self.response.status,
                  operation, function,
                  json.dumps(self.response.dict, indent=4, sort_keys=True)))
            return False

        dlog2("HTTP Status : %s %s Ok (%d)" %
              (operation, function, self.response.status))
        return True

    def _exit(self, code):
        """
        Exit the tool but not before closing an open Redfish
        client connection.

        :param code: the exit code
        :type code: int
        """

        if self.redfish_obj is not None and self.session is True:
            try:
                self.redfish_obj.logout()
                self.redfish_obj = None
                self.session = False
                dlog1("Session     : Closed")

            except Exception as ex:
                elog("Session close failed ; %s" % ex)
                alog("Check BMC username and password in config file")

        if code:
            sys.stdout.write("\n-------------------------------------------\n")

            # If exit with reason code then print that reason code and dump
            # the redfish query data that was learned up to that point
            elog("Code : %s" % code)

            # Root Query Info
            ilog("Root Query:\n%s\n" % self.root_query_info)

            # Managers Info
            ilog("Manager URL: %s" % self.managers_group_url)
            ilog("Manager Members List: %s" % self.manager_members_list)

            # Systems Info
            ilog("Systems Group URL: %s" % self.systems_group_url)
            ilog("Systems Member URL: %s" % self.systems_member_url)
            ilog("Systems Members: %d" % self.systems_members)
            ilog("Systems Members List: %s" % self.systems_members_list)

            ilog("Power State: %s" % self.power_state)
            ilog("Reset Actions: %s" % self.reset_action_dict)
            ilog("Reset Command URL: %s" % self.reset_command_url)
            ilog("Boot Control Dict: %s" % self.boot_control_dict)

            ilog("VM Members Array: %s" % self.vm_members_array)
            ilog("VM Group URL: %s" % self.vm_group_url)
            ilog("VM Group: %s" % self.vm_group)
            ilog("VM URL: %s" % self.vm_url)
            ilog("VM Label: %s" % self.vm_label)
            ilog("VM Version: %s" % self.vm_version)
            ilog("VM Actions: %s" % self.vm_actions)
            ilog("VM Media Types: %s\n" % self.vm_media_types)

            ilog("Last Response raw: %s\n" % self.response)
            ilog("Last Response json: %s\n" % self.response_json)

        rvmc_exit(code)

    ###########################################################################
    #
    #     P R I V A T E    S T A G E    M E M B E R    F U N C T I O N S
    #
    ###########################################################################

    ###########################################################################
    # Redfish Client Connect
    ###########################################################################
    def _redfish_client_connect(self):
        """
        Connect to target Redfish service.
        """

        stage = 'Redfish Client Connection'
        slog(stage)

        connect_error = False
        try:
            # One time Redfish Client Object Create
            self.redfish_obj = \
                redfish.redfish_client(base_url=self.uri,
                                       username=self.un,
                                       password=self.pw,
                                       default_prefix=REDFISH_ROOT_PATH)
            if self.redfish_obj is None:
                connect_error = True
                elog("Unable to establish %s to BMC at %s" %
                     (stage, self.uri))
        except Exception as ex:
            connect_error = True
            elog("Unable to establish %s to BMC at %s (%s)" %
                 (stage, self.uri, ex))

        if connect_error is True:
            alog("Check BMC ip address is pingable and supports Redfish")
            self._exit(1)

    ###########################################################################
    # Redfish Root Query
    ###########################################################################
    def _redfish_root_query(self):
        """
        Redfish Root Query
        """

        stage = 'Root Query'
        slog(stage)

        ilog("Progress    : %s, " % stage)

        if self.make_request(operation=GET, path=None) is False:
            elog("Failed %s GET request")
            self._exit(1)

        if self.response_json:
            self.root_query_info = self.response_json

        # extract the systems get url needed to learn reset
        # actions for the eventual reset.
        #
        # "Systems": { "@odata.id": "/redfish/v1/Systems/" },
        #
        # See Reset section below ; following iso insertion where
        # systems_group_url is used.
        self.systems_group_url = self.get_key_value('Systems', '@odata.id')

    ###########################################################################
    # Create Redfish Communication Session
    ###########################################################################
    def _redfish_create_session(self):
        """
        Create Redfish Communication Session
        """

        stage = 'Create Communication Session'
        slog(stage)

        try:
            self.redfish_obj.login(auth="session")
            dlog1("Session     : Open")
            self.session = True

        except Exception as ex:
            elog("Failed to Create session ; %s" % ex)
            self._exit(1)

    ###########################################################################
    # Query Redfish Managers
    ###########################################################################
    def _redfish_get_managers(self):
        """
        Query Redfish Managers
        """

        stage = 'Get Managers'
        slog(stage)

        # Virtual Media support is located through the
        # Managers link of the root query response.
        #
        # This section learns that Managers URL Link from the
        # Root Query Result:
        #
        # Expecting something like this ...
        #
        # {
        #    ...
        #    "Managers":
        #    {
        #        "@odata.id": "/redfish/v1/Managers/"
        #    },
        #    ...
        # }

        # Get Managers Link from the last Get response currently
        # in self.response_json
        self.managers_group_url = self.get_key_value('Managers', '@odata.id')
        if self.managers_group_url is None:
            elog("Failed to learn BMC RedFish Managers link")
            self._exit(1)

        # Managers Query (/redfish/v1/Managers/)
        if self.make_request(operation=GET,
                             path=self.managers_group_url) is False:
            elog("Failed GET Managers from %s" % self.managers_group_url)
            self._exit(1)

        # Look for the Managers 'Members' URL Link list from the Managers Query
        #
        # Expect something like this ...
        #
        # {
        #    ...
        #    "Members":
        #    [
        #         { "@odata.id": "/redfish/v1/Managers/1/" }
        #    ],
        #   ...
        # }
        # Support multiple Managers in the list

        self.manager_members_list = self.get_key_value('Members')

    ######################################################################
    # Get Systems Members
    ######################################################################
    def _redfish_get_systems_members(self):
        """
        Get Systems Members
        """

        stage = 'Get Systems'
        slog(stage)

        # Query Systems Group URL for list of Systems Members
        if self.make_request(operation=GET,
                             path=self.systems_group_url) is False:
            elog("Unable to %s Members from %s" %
                 (stage, self.systems_group_url))
            self._exit(1)

        self.systems_members_list = self.get_key_value('Members')
        dlog3("Systems Members List: %s" % self.systems_members_list)
        if self.systems_members_list is None:
            elog("Systems Members URL GET Response\n%s" % self.response_json)
            self._exit(1)

        self.systems_members = len(self.systems_members_list)
        if self.systems_members == 0:
            elog("BMC not publishing any System Members:\n%s" %
                 self.response_json)
            self._exit(1)

    ######################################################################
    # Power On or Off Host
    ######################################################################
    def _redfish_powerctl_host(self, state):
        """
        Power On or Off the Host
        """
        stage = 'Power ' + state + ' Host'
        slog(stage)

        if self.power_state == state:
            # already in required state
            return

        # Walk the Systems Members list looking for Action support.
        #
        #  "Members": [ { "@odata.id": "/redfish/v1/Systems/1/" } ],
        #
        # Loop over Systems Members List looking for Reset Actions Dictionary
        info = 'Redfish Systems Actions Member'
        self.systems_member_url = None
        for member in range(self.systems_members):
            systems_member = self.systems_members_list[member]
            if systems_member:
                self.systems_member_url = systems_member.get('@odata.id')
            if self.systems_member_url is None:
                elog("Unable to get %s URL:\n%s\n" %
                     (info, self.response_json))
                self._exit(1)

            if self.make_request(operation=GET,
                                 path=self.systems_member_url) is False:
                elog("Unable to get %s from %s" %
                     (info, self.systems_member_url))
                self._exit(1)

            # Look for Reset Actions Dictionary
            self.reset_action_dict = \
                self.get_key_value('Actions', '#ComputerSystem.Reset')
            if self.reset_action_dict is None:
                # try other URL
                self.systems_member_url = None
                continue
            else:
                # Got the Reset Actions Dictionary

                # get powerState
                self.power_state = self.get_key_value('PowerState')

                # Ensure we don't issue current state command
                if state in [POWER_OFF, POWER_ON]:
                    # This is a Power ON or Off command
                    if self.power_state == state:
                        dlog2("Power already %s" % state)
                        # ... AND we are already in that state then
                        # we are done. Issuing a power command while
                        # in the same state will error out.
                        # So don't do it.
                        return
                    ilog("Power %s" % state, False)
                break

        info = 'Systems Reset Action Dictionary'
        if self.reset_action_dict is None:
            elog("BMC not publishing %s:\n%s\n" %
                 (info, self.response_json))
            self._exit(1)

        ##############################################################
        # Reset Actions Dictionary. This is what we are looking for  #
        ##############################################################
        #
        # Look for Reset Actions label
        #
        # "Actions":
        # {
        #   "#ComputerSystem.Reset":
        #   {
        #     "ResetType@Redfish.AllowableValues": [
        #       "On",
        #       "ForceOff",
        #       "ForceRestart",
        #       "Nmi",
        #       "PushPowerButton"
        #     ],
        #     "target":"/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/"
        #   }
        # }
        #
        # Need to get 2 pieces of information out of the Actions output
        #
        #  1. the Redfish Systems Reset Action Target
        #  2. the Redfish Systems Reset Action List
        #
        ###############################################################

        info = 'Systems Reset Action Target'
        self.reset_command_url = self.reset_action_dict.get('target')
        if self.reset_command_url is None:
            elog("Unable to get Reset Command URL (members:%d)\n%s" %
                 (self.systems_members, self.reset_action_dict))
            self._exit(1)

        # With the reset target url in hand, all that is needed now
        # is the reset command this target supports
        #
        # The reset command list looks like this.
        #
        #        "ResetType@Redfish.AllowableValues": [
        #            "On",
        #            "ForceOff",
        #            "ForceRestart",
        #            "Nmi",
        #            "PushPowerButton"
        #        ],
        #
        # Some targets support GracefulRestart and/or ForceRestart

        info = 'Allowable Reset Actions'
        reset_command_list = \
            self.reset_action_dict.get('ResetType@Redfish.AllowableValues')
        if reset_command_list is None:
            elog("BMC is not publishing any %s" % info)
            self._exit(1)

        dlog3("ResetActions: %s" % reset_command_list)

        # load the appropriate acceptable command list
        acceptable_reset_commands = ['ForceRestart', 'GracefulRestart']
        acceptable_poweron_commands = ['ForceOn', 'On']
        acceptable_poweroff_commands = ['ForceOff', 'GracefulShutdown']
        if state == POWER_OFF:
            acceptable_commands = acceptable_poweroff_commands
        elif state == POWER_ON:
            acceptable_commands = acceptable_poweron_commands
        else:
            acceptable_commands = acceptable_reset_commands

        # Look for the best command for the power state requested.
        command = None
        for acceptable_command in acceptable_commands:
            for reset_command in reset_command_list:
                if reset_command == acceptable_command:
                    command = reset_command
                    break
            else:
                continue
            break

        if command is None:
            elog("Failed to find acceptable Power %s command in:\n%s" %
                 (state, reset_command_list))
            self._exit(1)

        # All that is left to do is POST the reset command
        # to the reset_command_url.
        payload = {'ResetType': command}
        if self.make_request(operation=POST,
                             payload=payload,
                             path=self.reset_command_url) is False:
            elog("Failed to Power %s Host" % state)
            self._exit(1)

        if state not in [POWER_OFF, POWER_ON]:
            # no need to refresh power state if
            # this was not a power command
            return

        dlog2("Verify      : %s" % stage)

        # delay and refresh self.power_state
        time.sleep(10)

        # get systems info
        if self.make_request(operation=GET,
                             path=self.systems_member_url) is False:
            elog("Failed to Get System State URL:%s" %
                 self.systems_member_url)
            self._exit(1)

        # get powerState
        self.power_state = self.get_key_value('PowerState')

    ######################################################################
    # Get CD/DVD Virtual Media URL
    ######################################################################
    def _redfish_get_vm_url(self):
        """
        Get CD/DVD Virtual Media URL from one of the Manager Members list
        """

        stage = 'Get CD/DVD Virtual Media'
        slog(stage)

        ilog("Get VM Info", False)

        if self.manager_members_list is None:
            elog("Unable to index Managers Members from %s" %
                 self.managers_group_url)
            self._exit(1)

        members = len(self.manager_members_list)
        if members == 0:
            elog("BMC is not publishing any redfish Manager Members")
            self._exit(1)

        # Issue a Get from each 'Manager Member URL Link looking
        # for supported virtual devices.
        for member in range(members):
            member_url = None
            this_member = self.manager_members_list[member]
            if this_member:
                member_url = this_member.get('@odata.id')
            if member_url is None:
                continue
            if self.make_request(operation=GET, path=member_url) is False:
                elog("Unable to get Manager Member from %s" % member_url)
                self._exit(1)

            ########################################################
            #                Query Virtual Media                   #
            ########################################################
            # Look for Virtual Media Support by this Manager Member
            #
            # Expect something like this ...
            #
            # {
            #    ...
            #    "VirtualMedia":
            #    {
            #        "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/"
            #    }
            #    ...
            # }
            self.vm_group_url = None
            self.vm_group = self.get_key_value('VirtualMedia')
            if self.vm_group is None:
                if (member + 1) == members:
                    elog("Virtual Media not supported by target BMC")
                    self._exit(1)
                else:
                    dlog3("Virtual Media not supported by member %d" % member)
                    continue
            else:
                try:
                    self.vm_group_url = self.vm_group.get('@odata.id')
                except Exception:
                    elog("Unable to get Virtual Media Group from %s" %
                         self.vm_group_url)
                    self._exit(1)

            # Query this member's Virtual Media Service Group
            if self.make_request(
                    operation=GET, path=self.vm_group_url) is False:
                elog("Failed to GET Virtual Media Service group from %s" %
                     self.vm_group_url)
                continue

            # Look for Virtual Media Device URL Links
            #
            # Expect something like this ...
            #
            # {
            #   ...
            #   "Members":
            #   [
            #       { "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/1/" },
            #       { "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2/" }
            #   ],
            #    ...
            # }
            self.vm_members_array = []
            try:
                self.vm_members_array = self.get_key_value('Members')
                vm_members = len(self.vm_members_array)
            except Exception:
                vm_members = 0

            if vm_members == 0:
                elog("No Virtual Media members found at %s" %
                     self.vm_group_url)
                self._exit(1)

            # Loop over each member's URL looking for the CD or DVD device
            # Consider trying the USB device as well if BMC supports that.
            for vm_member in range(vm_members):

                # Look for Virtual Media Device URL
                this_member = self.vm_members_array[vm_member]
                if this_member:
                    self.vm_url = this_member.get('@odata.id')

                if self.make_request(operation=GET, path=self.vm_url) is False:
                    elog("Failed to GET Virtual Media Service group from %s" %
                         self.vm_group_url)
                    continue

                # Query Virtual Media Device Type looking for supported device
                self.vm_media_types = self.get_key_value('MediaTypes')
                if self.vm_media_types is None:
                    dlog3("No Virtual MediaTypes found at %s ; "
                          "trying other members" % self.vm_url)
                    break

                dlog4("Virtual Media Service:\n%s" % self.response_json)

                if supported_device(self.vm_media_types) is True:
                    dlog3("Supported Virtual Media found at %s ; %s" %
                          (self.vm_url, self.vm_media_types))
                    break
                else:
                    dlog3("Virtual Media %s does not support CD/DVD ; "
                          "trying other members" % self.vm_url)
                    self.vm_url = None

            if self.vm_url is None:
                elog("Failed to find CD or DVD Virtual media type")
                self._exit(1)

    ######################################################################
    # Load Selected Virtual Media Version and Actions
    ######################################################################
    def _redfish_load_vm_actions(self):
        """
        Load Selected Virtual Media Version and Actions
        """

        stage = 'Load Selected Virtual Media Version and Actions'
        slog(stage)

        if self.vm_url is None:
            elog("Failed to find CD or DVD Virtual media type")
            self._exit(1)

        # Extract Virtual Media Version and Insert/Eject Actions
        #
        # Looks something like this. First half of odata.type is the VM label
        #
        # {
        #   ...
        #   "@odata.type": "#VirtualMedia.v1_2_0.VirtualMedia",
        #   "Actions": {
        #   "#VirtualMedia.EjectMedia":
        #   {
        #     "target" :
        #     ".../Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia/"
        #   },
        #   "#VirtualMedia.InsertMedia":
        #   {
        #     "target":
        #     ".../Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia/"
        #   }
        #   ...
        # },
        vm_data_type = self.get_key_value('@odata.type')
        if vm_data_type:
            self.vm_label = vm_data_type.split('.')[0]
            self.vm_version = vm_data_type.split('.')[1]
            self.vm_actions = self.get_key_value('Actions')
        dlog1("VM Version  : %s" % self.vm_version)
        dlog1("VM Label    : %s" % self.vm_label)
        dlog3("VM Actions  :\n%s\n" % self.vm_actions)

    ######################################################################
    # Power Off Host
    ######################################################################
    def _redfish_poweroff_host(self):
        """
        Power Off the Host
        """

        self._redfish_powerctl_host(POWER_OFF)

    ######################################################################
    # Eject Current Image
    ######################################################################
    def _redfish_eject_image(self):
        """
        Eject Current Image
        """

        stage = 'Eject Current Image'
        slog(stage)

        current_image = self.get_key_value('Image')
        if current_image:
            vm_eject = self.vm_actions.get('#VirtualMedia.EjectMedia')
            if vm_eject:
                vm_eject_url = vm_eject.get('target')
                if vm_eject_url:
                    ilog("Eject CD/DVD", False)
                    if self.make_request(operation=POST,
                                         payload={},
                                         path=vm_eject_url) is False:
                        elog("Failed to GET Virtual Media Eject URL from %s" %
                             vm_eject_url)

    ######################################################################
    # Insert Image into Virtual Media CD/DVD
    ######################################################################
    def _redfish_insert_image(self):
        """
        Insert Image into Virtual Media CD/DVD
        """

        stage = 'Insert Image into Virtual Media CD/DVD'
        slog(stage)

        ilog("Insert Image", False)

        vm_insert_url = None
        vm_insert_act = self.vm_actions.get('#VirtualMedia.InsertMedia')
        if vm_insert_act:
            vm_insert_url = vm_insert_act.get('target')

        if vm_insert_url is None:
            elog("Unable to get Virtual Media Insertion URL\n%s\n" %
                 self.response_json)
            self._exit(1)

        payload = {'Image': self.img,
                   'Inserted': True,
                   'WriteProtected': True}
        if self.make_request(operation=POST,
                             payload=payload,
                             path=vm_insert_url) is False:
            elog("Failed to Insert Media")
            self._exit(1)

        dlog2("Verify      : %s" % stage)

        if self.make_request(operation=GET, path=self.vm_url) is False:
            elog("Unable to verify Image insertion" % self.vm_url)
            self._exit(1)

        if self.get_key_value('Image') != self.img:
            elog("Insertion verification failed.")
            ilog("Expected Image: %s" % self.img)
            ilog("Detected Image: %s" % self.get_key_value('Image'))
            self._exit(1)

        # Verify Insertion
        #
        # Looking for the following values
        #
        dlog3("Image URI   : %s" % self.get_key_value('Image'))
        dlog3("ImageName   : %s" % self.get_key_value('ImageName'))
        dlog3("Inserted    : %s" % self.get_key_value('Inserted'))
        dlog3("Protected   : %s" % self.get_key_value('WriteProtected'))

    ######################################################################
    # Set Next Boot Override to CD/DVD
    ######################################################################
    def _redfish_set_boot_override(self):
        """
        Set Next Boot Override to CD/DVD
        """

        stage = 'Set Next Boot Override to CD/DVD"'
        slog(stage)

        ilog("Set CD/DVD Boot", False)

        # Walk the Systems Members list looking for Boot support.
        #
        #  "Members": [ { "@odata.id": "/redfish/v1/Systems/1/" } ],
        #
        # Loop over Systems Members List looking for Boot Dictionary
        info = 'Systems Boot Member'
        for member in range(self.systems_members):

            self.systems_member_url = None
            systems_member = self.systems_members_list[member]
            if systems_member:
                self.systems_member_url = systems_member.get('@odata.id')
            if self.systems_member_url is None:
                elog("Unable to get %s from %s" %
                     (info, self.systems_members_list))
                self._exit(1)

            if self.make_request(operation=GET,
                                 path=self.systems_member_url) is False:
                elog("Unable to get %s from %s" %
                     (info, self.systems_member_url))
                self._exit(1)

            # Look for Reset Actions Dictionary
            self.boot_control_dict = self.get_key_value('Boot')
            if self.boot_control_dict is None:
                continue

        if self.boot_control_dict is None:
            elog("Unable to get %s from %s" % (info, self.systems_member_url))
            self._exit(1)

        payload = {"Boot": {"BootSourceOverrideEnabled": "Once",
                            "BootSourceOverrideTarget": "Cd"}}
        if self.make_request(operation=PATCH,
                             path=self.systems_member_url,
                             payload=payload) is False:
            elog("Unable to verify Image insertion" % self.vm_url)
            self._exit(1)

        dlog2("Verify      : %s" % stage)

        if self.make_request(operation=GET,
                             path=self.systems_member_url) is False:
            elog("Unable to verify Boot Override insertion" % self.vm_url)
            self._exit(1)

    ######################################################################
    # Power On Host
    ######################################################################
    def _redfish_poweron_host(self):
        """
        Power On or Off the Host
        """

        self._redfish_powerctl_host(POWER_ON)

    def execute(self):
        """The main controller function that executes the iso insertion
        algorithm for the specified target object (self)"""

        self._redfish_client_connect()
        self._redfish_root_query()
        self._redfish_create_session()
        self._redfish_get_managers()
        self._redfish_get_systems_members()
        self._redfish_get_vm_url()
        self._redfish_load_vm_actions()
        self._redfish_eject_image()
        self._redfish_poweroff_host()
        self._redfish_insert_image()
        self._redfish_set_boot_override()
        self._redfish_poweron_host()

        ilog("Done", False)

        if self.redfish_obj is not None and self.session is True:
            self.redfish_obj.logout()
            dlog1("Session     : Closed")


##############################################################################
#
# Load BMC target info from Config File.
# For each BMC target create target object through parse_target.
#     Add each created target object to target_object_list.
# Insert BMC iso for each object in target_object_list through self.execute
#
##############################################################################
# Find, Open and Read callers config file
# ---------------------------------------
cfg = None
if not os.path.exists(CONFIG_FILE):
    elog("Unable to find specified config file: %s" % CONFIG_FILE)
    alog("Check config file spelling and presence\n\n")
    rvmc_exit(1)
try:
    with open(CONFIG_FILE, 'r') as yaml_config:
        dlog1("Config File : %s" % CONFIG_FILE)
        cfg = yaml.safe_load(yaml_config)
        dlog3("Config Data: %s" % cfg)
except Exception as ex:
    elog("Unable to open specified config file: %s (%s)" %
         (CONFIG_FILE, ex))
    alog("Check config file access and permissions.\n\n")
    rvmc_exit(1)


# Parse the config file
# ----------------------
found = False  # assume nothing is found to start

# loop over all the sections looking for the primary config label
for section in cfg:
    if section == PRIMARY_CONFIG_LABEL:
        # ... once found then loop over all the targets
        dlog2("VM Iso Label: %s" % cfg[section])
        found = True
        if targets:
            dlog1("Using specified target(s): %s" % targets)
        else:
            for target in cfg[section]:
                targets.append(target)

        dlog1("Targets  : %s" % targets)
        for target in targets:
            try:
                parse_target(target, cfg[section][target])
            except Exception as ex:
                elog("Failed to parse info from '%s' target %s" % (target, ex))
                alog("Verify %s file has %s target and such target "
                     "is properly formatted" %
                     (CONFIG_FILE, target))
                continue

# 'found' would still be false if the config file is for a single target
if found is False:
    dlog3("Try single")
    parse_target(None, cfg)

if len(target_object_list):
    # Load the Iso for all loaded objects
    for targetObj in target_object_list:
        if targetObj.target is not None:
            ilog("BMC Target  : %s" % targetObj.target)
        if debug == 0:
            ilog("BMC IP Addr : %s" % targetObj.ip)
            ilog("Host Image  : %s" % targetObj.img)
        targetObj.execute()
else:
    elog("Operation aborted ; no valid bmc information found")
    if CONFIG_FILE and cfg:
        ilog("Config File :\n%s" % cfg)
    rvmc_exit(1)

rvmc_exit(0)
