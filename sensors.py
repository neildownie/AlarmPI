#!/usr/bin/env python

import RPi.GPIO as GPIO
import threading
import time

import requests
import re

from colors import bcolors
import paho.mqtt.client as mqtt


class outputGPIO():
    def enableOutputPin(self, *pins):
        for pin in pins:
            GPIO.setup(pin, GPIO.OUT)
            state = GPIO.input(pin)
            if state == GPIO.LOW:
                GPIO.output(pin, GPIO.HIGH)
            self.outputPins[pin] = {'state': state, 'type': 'GPIO.output'}

    def disableOutputPin(self, *pins):
        for pin in pins:
            if pin in self.outputPins:
                GPIO.setup(pin, GPIO.OUT)
                if GPIO.input(pin) == GPIO.HIGH:
                    GPIO.output(pin, GPIO.LOW)
                GPIO.setup(pin, GPIO.IN)
                del self.outputPins[pin]

    def getOutputPinStates(self):
        return self.outputPins


class sensorGPIO():
    def __init__(self, sensorName):
        # GPIO Setup
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # Global Required Variables
        self.sensorName = sensorName
        self.online = True
        self.alert = None
        self._event_alert = []
        self._event_alert_stop = []
        self._event_error = []
        self._event_error_stop = []

        # Other Variables
        self.gpioState = None
        self.pin = None

    def getOnlineStatus(self):
        return self.online

    def getAlertStatus(self):
        return self.alert

    def setAlertStatus(self):
        self.gpioState = GPIO.input(self.pin)
        self.alert = False
        if self.gpioState == 1:
            self.alert = True

    def add_sensor(self, sensor, settings=None):
        self.pin = int(sensor['pin'])
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.remove_event_detect(self.pin)
        GPIO.add_event_detect(
            self.pin, GPIO.BOTH,
            callback=self._checkInputPinState,
            bouncetime=600)
        self._checkInputPinState(self.pin)
        self.setAlertStatus()

    def reload(self, settings=None):
        pass

    def del_sensor(self):
        GPIO.remove_event_detect(self.pin)

    def _checkInputPinState(self, inputPin):
        nowState = GPIO.input(self.pin)
        if nowState != self.gpioState:
            # print("Pin: {0} changed state to: {1}".format(self.pin, str(nowState)))
            if nowState == 1:
                self._notify_alert()
            else:
                self._notify_alert_stop()
        else:
            print(bcolors.STRIKE + "Wrong state change. Ignoring!!!" + bcolors.ENDC)

    def forceNotify(self):
        # if GPIO.input(self.pin) == 1:
        #     self._notify_alert()
        # else:
        #     self._notify_alert_stop()
        pass

    # ------------------------------
    def on_alert(self, callback):
        self._event_alert.append(callback)

    def on_alert_stop(self, callback):
        self._event_alert_stop.append(callback)

    def on_error(self, callback):
        pass

    def on_error_stop(self, callback):
        pass

    def _notify_alert(self):
        self.setAlertStatus()
        for callback in self._event_alert:
            callback(self.sensorName)

    def _notify_alert_stop(self):
        self.setAlertStatus()
        for callback in self._event_alert_stop:
            callback(self.sensorName)

    def _notify_error(self):
        pass

    def _notify_error_stop(self):
        pass


class sensorHikvision():
    def __init__(self, sensorName):
        # Global Required Variables
        self.sensorName = sensorName
        self.online = None
        self.alert = False
        self._event_alert = []
        self._event_alert_stop = []
        self._event_error = []
        self._event_error_stop = []

        # Other Variables
        self.alertTime = 8
        self.threadRunforever = None
        self.runforever = None
        self.hasBeenNotified = False
        self.sensor = None

    def add_sensor(self, sensor, settings=None):
        self.sensor = sensor
        self.reload()

    def reload(self, settings=None):
        self.runforever = False
        ip = self.sensor['ip']
        username = self.sensor['user']
        password = self.sensor['pass']
        self.threadRunforever = threading.Thread(target=self.runInBackground, args=[
                                                 self.sensor, ip, username, password])
        self.threadRunforever.daemon = True
        self.threadRunforever.start()
        self._notify_alert_stop()

    def runInBackground(self, sensor, ip, username, password):
        self.runforever = True
        authorization = requests.auth.HTTPBasicAuth(username, password)
        while self.runforever:
            try:
                response = requests.get('http://' + ip + '/ISAPI/Event/notification/alertStream',
                                        auth=authorization,
                                        timeout=5,
                                        stream=True)
                if not self.online:
                    self._notify_error_stop()
                for chunk in response.iter_lines():
                    if chunk:
                        chunk = chunk.decode("utf-8")
                        match = re.match(r'<eventType>(.*)</eventType>', chunk)
                        if match:
                            if match.group(1) == 'linedetection':
                                if not self.hasBeenNotified:
                                    self._notify_alert()
            except Exception as e:
                if self.online:
                    self._notify_error()
                print(bcolors.WARNING, e, bcolors.ENDC)
                time.sleep(5)

    def del_sensor(self):
        self.runforever = False

    def forceNotify(self):
        # self._notify_alert_stop()
        pass

    # ------------------------------
    def on_alert(self, callback):
        self._event_alert.append(callback)

    def on_alert_stop(self, callback):
        self._event_alert_stop.append(callback)

    def on_error(self, callback):
        self._event_error.append(callback)

    def on_error_stop(self, callback):
        self._event_error_stop.append(callback)

    def _notify_alert(self):
        self.hasBeenNotified = False
        self.alert = True
        threading.Thread(target=self._notify_alert_stop_later).start()
        for callback in self._event_alert:
            callback(self.sensorName)

    def _notify_alert_stop(self):
        self.alert = False
        self.hasBeenNotified = True
        for callback in self._event_alert_stop:
            callback(self.sensorName)

    def _notify_alert_stop_later(self):
        time.sleep(self.alertTime)
        self._notify_alert_stop(self.sensorName)

    def _notify_error(self):
        self.online = False
        for callback in self._event_error:
            callback(self.sensorName)

    def _notify_error_stop(self):
        self.online = True
        for callback in self._event_error_stop:
            callback(self.sensorName)


class sensorMQTT():
    def __init__(self, sensorName):
        # Global Required Variables
        self.sensorName = sensorName
        self.online = None
        self.alert = False
        self._event_alert = []
        self._event_alert_stop = []
        self._event_error = []
        self._event_error_stop = []

        # Other Variables
        self.mqttclient = mqtt.Client("", True, None, mqtt.MQTTv31)
        self.sensor = None

    def add_sensor(self, sensor, settings=None):
        self._notify_error()
        self.sensor = sensor
        self.mqttclient.on_connect = self.on_connect
        self.mqttclient.on_message = self.on_message
        self.reload(settings)

    def del_sensor(self):
        self.runforever = False

    def forceNotify(self):
        # self._notify_alert_stop()
        pass

    def reload(self, settings=None):
        self.mqttsettings = settings
        self.mqttclient.disconnect()
        self.mqttclient.loop_stop(force=False)
        if self.mqttsettings['enable']:
            try:
                mqttHost = self.mqttsettings['host']
                mqttPort = self.mqttsettings['port']
                self.mqttclient.connect(mqttHost, mqttPort, 60)
                self.mqttclient.loop_start()
            except Exception as e:
                print(e)
        else:
            self.mqttclient.disconnect()
            self.mqttclient.loop_stop(force=False)

    def on_connect(self, mqttclient, userdata, flags, rc):
        print("Sensor connected to MQTT with result code " + str(rc))
        mqttclient.subscribe(self.sensor['state_topic'])

    def on_message(self, mqttclient, userdata, msg):
        # print(msg.topic + " ------- " + msg.payload.decode("utf-8"))
        if msg.payload.decode("utf-8") == self.sensor["message_alert"]:
            self._notify_alert()
        elif msg.payload.decode("utf-8") == self.sensor["message_noalert"]:
            self._notify_alert_stop()
        else:
            self._notify_error()

    # ------------------------------
    def on_alert(self, callback):
        self._event_alert.append(callback)

    def on_alert_stop(self, callback):
        self._event_alert_stop.append(callback)

    def on_error(self, callback):
        self._event_error.append(callback)

    def on_error_stop(self, callback):
        self._event_error_stop.append(callback)

    def _notify_alert(self):
        self.alert = True
        for callback in self._event_alert:
            callback(self.sensorName)

    def _notify_alert_stop(self):
        self.alert = False
        for callback in self._event_alert_stop:
            callback(self.sensorName)

    def _notify_error(self):
        self.online = False
        for callback in self._event_error:
            callback(self.sensorName)

    def _notify_error_stop(self):
        self.online = True
        for callback in self._event_error_stop:
            callback(self.sensorName)


class Sensor():
    """docstring for Sensor"""

    def __init__(self):
        self._event_alert = []
        self._event_alert_stop = []
        self._event_error = []
        self._event_error_stop = []
        self.allSensors = {}

    def add_sensors(self, settings):
        self.settings = settings
        sensors = settings['sensors']
        for sensor, sensorvalues in sensors.items():
            if sensor not in self.allSensors:
                sensorType = sensorvalues['type']
                sensorName = sensorvalues['name']
                print("{0}{1} {2} sensor with id: {3}{4}".format(
                    bcolors.OKBLUE, sensorType, sensorName, sensor, bcolors.ENDC))
                if sensorType == 'GPIO':
                    sensorobject = sensorGPIO(sensor)
                    sensorsettings = None
                elif sensorType == 'Hikvision':
                    sensorobject = sensorHikvision(sensor)
                    sensorsettings = None
                elif sensorType == 'MQTT':
                    sensorobject = sensorMQTT(sensor)
                    sensorsettings = settings['mqtt']
                self.allSensors[sensor] = {
                    'values': sensorvalues,
                    'obj': sensorobject,
                    'settings': sensorsettings
                }
                self.allSensors[sensor]['obj'].on_alert(self._notify_alert)
                self.allSensors[sensor]['obj'].on_alert_stop(
                    self._notify_alert_stop)
                self.allSensors[sensor]['obj'].on_error(self._notify_error)
                self.allSensors[sensor]['obj'].on_error_stop(
                    self._notify_error_stop)
                self.allSensors[sensor]['obj'].add_sensor(
                    self.allSensors[sensor]['values'],
                    self.allSensors[sensor]['settings'])
            self.allSensors[sensor]['obj'].forceNotify()

    def del_sensor(self, sensor):
        self.allSensors[sensor]['obj'].del_sensor()
        del self.allSensors[sensor]

    def reload(self, sensortype=None, settings=None):
        for sensor in self.allSensors:
            if sensortype is None or sensortype == self.allSensors[sensor]['values']['type']:
                print(self.allSensors[sensor]['values']['name'])
                self.allSensors[sensor]['obj'].reload(settings)

    def get_all_sensors(self):
        return self.allSensors

    # ------------------------------
    def on_alert(self, callback):
        self._event_alert.append(callback)

    def on_alert_stop(self, callback):
        self._event_alert_stop.append(callback)

    def on_error(self, callback):
        self._event_error.append(callback)

    def on_error_stop(self, callback):
        self._event_error_stop.append(callback)

    def _notify_alert(self, sensorName):
        for callback in self._event_alert:
            callback(sensorName)

    def _notify_alert_stop(self, sensorName):
        for callback in self._event_alert_stop:
            callback(sensorName)

    def _notify_error(self, sensorName):
        self.online = False
        for callback in self._event_error:
            callback(sensorName)

    def _notify_error_stop(self, sensorName):
        self.online = True
        for callback in self._event_error_stop:
            callback(sensorName)