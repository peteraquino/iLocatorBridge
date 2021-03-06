#!/usr/bin/env python

from pyicloud import PyiCloudService
from math import radians, cos, sin, asin, sqrt
from datetime import datetime
from datetime import timedelta
import ConfigParser
import sys
import os
import time
import requests
import base64
import logging
import argparse

#DEFAULT_CONFIG = 'configuration.ini'
#DEFAULT_LOGFILE = 'iLocatorLog.log'

logger = logging.getLogger('iLocator')


def configurationManager(configfile):
    gConfig = ConfigParser.ConfigParser()
    try:
        gConfig.read(configfile)
        logging.info('Configuration %s loaded' % (configfile, ))
        if logger.isEnabledFor(logging.DEBUG):
            for section in gConfig.sections():
                logger.debug('Configuration[%s] %s' % (section, configSectionMap(gConfig, section)))

    except Exception, e:
        print('Exception! Please check the log: %s' % (e, ))
        logger.error('\r\nNo configuration avaialble. Please see https://github.com/trusk89/iLocatorBridge for configuration')
        sys.exit(0)
    
    #get the LocationItems...
    locationItems = parseMultipleSections(gConfig, 'LocationItems')

    # to stay backward compatible
    geoFences = parseMultipleSections(gConfig, 'Geofence')
    for fenceId, fence in geoFences.items():
        if 'homelatitude' in fence or 'homelongitude' in fence:
            for k in ['Latitude', 'Longitude']:
                oldkey = 'home%s' % k.lower()
                fence[k.lower()] = fence.get(oldkey, fence.get(k.lower()))

            print "Warning: found HomeLatitude/HomeLongitude in Section [Geofence%s] " % (fenceId, )
            print " - this is deprecated please use Lattitude/Longitude instead. exp: \n"
            print "[Geofence%s]" % fenceId
            for k, v in fence.items():
                if not k.startswith('home'):
                    print '%s: %s' % (k, v)
            print ""

    return (
        configSectionMap(gConfig, 'iCloud'),
        geoFences,
        configSectionMap(gConfig, 'OpenHab'),
        locationItems,
        )


def parseMultipleSections(gconfig, marker):
    return dict([
                 (section[len(marker):], configSectionMap(gconfig, section))
                 for section in gconfig.sections()
                 if section.startswith(marker)
                 ])


def configSectionMap(gConfig, section):
    dict = {}
    options = gConfig.options(section)
    for option in options:
        try:
            dict[option] = gConfig.get(section, option)
        except:
            print('Exception! Please check the log')
            logger.error('exception on %s!' % option)
            sys.exit(0)
    logger.info('Configuration %s parsed' % (section))
    return dict


def getDeviceCoordinates(gRequester, deviceId, deviceName):
    locationDictionary = None
    errorCount = 0
    
    RetriesBeforeRestart = int(gConfigurationOH['retriesbeforerestart'])
    StatusItem = gConfigurationOH.get('ohitem_status')
    
    #Initialize empty location items...
    CoordinatesItem = None
    AccuracyItem = None
    
    #See if there are any configured LocationItems for this device...
    for locationItemID, locationItem in gConfigurationLocationItems.items():
        #If found, assign them to the initialized vars...
        if 'deviceid'+ str(deviceName).strip() == locationItem.get('device').strip().lower():
            CoordinatesItem = locationItem.get('ohitem_coordinates')
            AccuracyItem = locationItem.get('ohitem_accuracy')
            #then exit the for loop...
            break

    while locationDictionary is None:
        try:
            locationDictionary = (gRequester.devices[deviceId].location())
    
        except Exception, e:
            errorCount = errorCount + 1
            print('Exception! Please check the log')
            logger.error('Could not get device coordinates. Retrying!: %s' % (e, ))
            #If configured, post error to OpenHAB status item...
            if StatusItem: postUpdate(StatusItem, str(e))
            
            #If we've passed the RetriesBeforeRestart count, restart the script...
            logger.info(str('Error count: %s' % (errorCount)))
            if RetriesBeforeRestart > 0 and int(errorCount) >= RetriesBeforeRestart :
                restartProgram()
            
            #If configured, send the next poll time to OpenHAB based on the config RetryInterval value...
            RetryPollTimeItem = gConfigurationOH.get('ohitem_nextpolltime')
            if RetryPollTimeItem:
                RetryPollTime = datetime.now() + timedelta(seconds=int(gConfigurationOH['retryinterval']))
                postUpdate(RetryPollTimeItem, str(RetryPollTime))

            time.sleep(int(gConfigurationOH['retryinterval']))

        pass

    #If configured, post 'Active' to OpenHAB status item...
    if StatusItem: postUpdate(StatusItem, 'Active')
    #If configured, post lat/ling coordinates to OpenHAB...
    if CoordinatesItem: postUpdate(CoordinatesItem, str(locationDictionary['latitude']) + ',' + str(locationDictionary['longitude']))
    #If configured, post accuracy to OpenHAB...
    if AccuracyItem: postUpdate(AccuracyItem, str(convertDistance(locationDictionary['horizontalAccuracy'],gConfigurationOH['distanceunit'])))
    
    return float(locationDictionary['latitude']), float(locationDictionary['longitude'])


def getDistance (lat, longitude, geoFence):
    return convertDistance(haversine(geoFence['latitude'], lat, geoFence['longitude'], long),gConfigurationOH['distanceunit'])


def convertDistance(meters,desiredUnit):
    if desiredUnit == 'km':
        return meters * .001
    elif desiredUnit == 'ft':
        return meters * 3.28084
    elif desiredUnit == 'mi':
        return meters * 0.000621371
    elif desiredUnit == 'nm':
        return meters * 0.000539957
    else:
        return meters


def isInGeofence(distance, geoFence):
    if float(distance) <= float(geoFence['geofenceradius']):
        return True
    else:
        return False


def haversine(lat1, lat2, lon1, lon2):
    # Thanks for this Aaron D
    # http://stackoverflow.com/questions/15736995/how-can-i-quickly-estimate-the-distance-between-two-latitude-longitude-points
    lon1 = (float(lon1))
    lat1 = (float(lat1))
    lon2 = (float(lon2))
    lat2 = (float(lat2))
    # convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    c = 2 * asin(sqrt(a))
    m = 6378.137 * c * 1000
    return m


def postUpdate(variable, state):
    global gConfigurationOH

    url = '%s/rest/items/%s/state' % (gConfigurationOH['ohserver'], variable)
    try:
        req = requests.put(url, data=state, headers=basic_header())
        if req.status_code != requests.codes.ok:
            req.raise_for_status()
        logger.info('Update posted to OpenHab: %s = %s' %(variable,state))
    except Exception, e:
        print('Exception! Please check the log. Will continue execution.')
        logger.error('Could not post update to OpenHab: %s' % (e, ))


def restartProgram():
    try:
        logger.info('Restarting script...')
        os.execv(__file__, sys.argv)

    except Exception, e:
        logger.error('Restart program error: %s' % (e, ))

    return True


def basic_header():
    global gConfigurationOH

    auth = base64.encodestring('%s:%s' % (gConfigurationOH['ohusername'], gConfigurationOH['ohpassword'])).replace('\n', '')
    return {"Authorization": "Basic %s" % auth,"Content-type": "text/plain"}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='iLocatorBridge - Bridge between iCloud location and OpenHAB')
    parser.add_argument('-c', '--config', dest='config', default=DEFAULT_CONFIG,help='Config location (default: %s)' % (DEFAULT_CONFIG, ))
    parser.add_argument('-v', '--verbose', dest='verbose', action='store_true',help='Be more verbose in the output')
    parser.add_argument('--list-devices', dest='listDevices', action='store_true',help='Do not update anything, just prints all existing device ids')

    args = parser.parse_args()
    logging.basicConfig(
        filename=DEFAULT_LOGFILE, level=args.verbose and logging.DEBUG or logging.INFO,
        format='%(asctime)s %(message)s')

    gConfigurationiCloud, gConfigurationGeofence, gConfigurationOH, gConfigurationLocationItems = configurationManager(args.config)
    gRequester = PyiCloudService(gConfigurationiCloud['username'], gConfigurationiCloud['password'])

    if args.listDevices:
        # make it easy to get the device ids for config
        devices = gRequester.devices
        for idx, device in enumerate(devices.keys()):
            print 'device%d: %s  # %s' % (idx, device, devices[device])
    else:
        # get the first configured device for default case
        devices = [d for d in sorted(gConfigurationiCloud.keys()) if d.startswith('deviceid')]
        defaultDevice = devices[0]
        logging.debug('Default Device: %s of %s' % (defaultDevice, devices))

        while 1:
            try:
                minDesiredPollRate = -1
                coordCache = {}
                #get the OH items for current poll rate and next poll time
                variablepollingrate = gConfigurationOH.get('ohitem_pollingrate')
                variablenextpolltime = gConfigurationOH.get('ohitem_nextpolltime')
                #Loop through the geofences...
                for geoId, geoFence in gConfigurationGeofence.items():
                    # look for device overwrite on fence
                    device = geoFence.get('device') or defaultDevice
                    deviceName = device.lower() == 'deviceid' and '' or '%s ' % device[len('deviceid'):]
                    deviceId = gConfigurationiCloud[device.lower()]

                    # look for variable overwrite on fence
                    variable = geoFence.get('ohitem_presence') or gConfigurationOH.get('ohitem')
                    variablepollingmap = geoFence.get('pollingmap')
                    variabledistance = geoFence.get('ohitem_distance')

                    if not variable:
                        msg = "No OHItem is definded not in [Geofence%s] or [OpenHab]" % (geoId)
                        print msg
                        logging.error(msg)
                        continue

                    logging.debug('Device: %s(%s) -> %s' % (deviceName, variable, geoFence))

                    lat, long = coordCache[device] = getDeviceCoordinates(gRequester, deviceId, deviceName)

                    logging.info('Device %slocated @ %s,%s' % (deviceName, lat,long))
                    
                    #Get the actual distance from base coordinates and post it if OHItem_Distance provided...
                    CurrentDistance = getDistance(lat, long, geoFence)
                    if variabledistance: postUpdate(variabledistance, str(CurrentDistance))
                    
                    if isInGeofence(CurrentDistance, geoFence) is True:
                        logging.info('Device %sis in Geofence %s' % (deviceName, geoId))
                        postUpdate(variable, 'ON')
                    else:
                        logging.info('Device %sis outside of Geofence %s' % (deviceName, geoId))
                        postUpdate(variable, 'OFF')

                    #Set a default polling rate to the max in the PollingMap (in case the current distance is > the max defined distance in the polling map)
                    PollingMap = geoFence['pollingmap']
                    keyvaluepair = PollingMap.split(",")
                    last = (len(keyvaluepair) - 1)
                    PollingRate = keyvaluepair[last].split("=")[1]
                                
                    #Loop through the polling map and find the interval that corresponds to the current distance...
                    for interval in PollingMap.split(","):
                        dist, rate = interval.split("=")
                        if float(CurrentDistance) <= float(dist):
                            PollingRate = rate
                            break
                                                    
                    logging.info('Desired polling rate for Geofence %s is %s' % (geoId, PollingRate))
                    #Is the polling rate for this geofence less than any other geofence?...
                    if minDesiredPollRate==-1:
                        minDesiredPollRate=PollingRate
                    else:
                        if PollingRate < minDesiredPollRate: minDesiredPollRate = PollingRate
                                                                        
                    #Now we have the calculated poll time, based on current distance and polling map, calculate the next time we will poll
                    NextPollTime = datetime.now() + timedelta(seconds=int(minDesiredPollRate))
                                                                            
                    #log the current poll rate and next poll time and update the OpenHAB items if they were provided in the configuration file...
                    logging.info('Current Polling Rate: ' + str(minDesiredPollRate))
                    logging.info('Next Poll Time: %s' % (NextPollTime))
                    if variablepollingrate: postUpdate(variablepollingrate, str(minDesiredPollRate))
                    if variablenextpolltime: postUpdate(variablenextpolltime, str(NextPollTime))
                                                                                            
                #wait until the next poll time...
                time.sleep(int(minDesiredPollRate))

            except Exception, e:
                restartProgram()

