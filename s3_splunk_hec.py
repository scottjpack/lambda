import logging
import json
import socket
import time
import urllib2
import ssl
import os
import boto3

_max_content_bytes = 100000
http_event_collector_SSL_verify = True
http_event_collector_debug = False

"""This function will retrieve files placed into an S3 bucket and send them to Splunk via the HTTP Event Collector
It circumvents some issues encountered by the Splunk AWS TA when attempting to ingest buckets with large amounts of data
Note that this will incur costs per execution, and can ramp up to thousands of dollars/month in really busy buckets.
You may also need to get AWS Support to increase your invocation limit.
Seriously, it can really go wild, but is unmatched in it's ability to push logs from S3 to your Splunk deployment.

Required environment vars:
- index   (Splunk Index, token must have access)
- sourcetype
- token
- indexer (IE: splunk-hec.example.com)

Set this lambda function to trigger on PutObject for the S3 bucket you want to monitor.
Ensure the role the function executes as has permissions to read the bucket.

It will the default port, (8088)
Thanks AGAIN to George Starcher of Defense Point Security for use of his HEC class.
"""


log = logging.getLogger(__name__)

def extract_keys(event):
    keys = []
    if not "Records" in event:
        print "No records in event"
        keys = []
    else:
        for record in event['Records']:
            bucket = record['s3']['bucket']['name']
            key = record['s3']['object']['key']
            keys.append(
                {
                    "key":key,
                    "bucket":bucket
                }
            )
    return keys 

def splunk_s3_key(key, s3Client, opts):
    bucket = key['bucket']
    key = key['key']
    obj = s3Client.get_object(Bucket=bucket, Key=key)
    s = obj['Body'].read()
    events = s.splitlines()
    source = "s3://%s/%s" % (bucket, key)
    new_opts = opts
    new_opts['source'] = source
    send_splunk(events, opts)
    

def lambda_handler(event, context):
    # event needs to have an opts dictionary with indexer, token, index, and sourcetype, 
    new_keys = extract_keys(event)
    opts = get_hec_settings()
    s3Client = boto3.client('s3')
    for key in new_keys:
        splunk_s3_key(key, s3Client, opts)
    return


def get_hec_settings():
    hec_config = {}
    hec_config['index'] = os.environ['index']
    hec_config['sourcetype'] = os.environ['sourcetype']
    hec_config['token'] = os.environ['token']
    hec_config['indexer'] = os.environ['indexer']
    return hec_config


def send_splunk(events, opts, index_override=None, sourcetype_override=None):
  #Get Splunk Options
  logging.info("Options: %s" % json.dumps(opts))
  http_event_collector_key = opts['token']
  http_event_collector_host = opts['indexer']
  #Set up the collector
  splunk_event = http_event_collector(http_event_collector_key, http_event_collector_host)

  #init the payload
  payload = {}

  for event in events:
    #Set up the event metadata
    if index_override is None:
      payload.update({"index":opts['index']})
    else:
      payload.update({"index":index_override})
    if sourcetype_override is None:
      payload.update({"sourcetype":opts['sourcetype']})
    else:
      payload.update({"index":sourcetype_override})
    if "sourcetype" in opts:
      payload.update({"source":opts['source']})

    #Add the event
    payload.update({"event":event})
    #fire it off
    splunk_event.batchEvent(payload)
  splunk_event.flushBatch()


class http_event_collector:

    def __init__(self,token,http_event_server,host="",http_event_port='8088',http_event_server_ssl=True,max_bytes=_max_content_bytes):
        self.token = token
        self.batchEvents = []
        self.maxByteLength = max_bytes
        self.currentByteLength = 0

        # Set host to specified value or default to localhostname if no value provided
        if host:
            self.host = host
        else:
            self.host = socket.gethostname()

        # Build and set server_uri for http event collector
        # Defaults to SSL if flag not passed
        # Defaults to port 8088 if port not passed

        if http_event_server_ssl:
            buildURI = ['https://']
        else:
            buildURI = ['http://']
        for i in [http_event_server,':',http_event_port,'/services/collector/event']:
            buildURI.append(i)
        self.server_uri = "".join(buildURI)


    def sendEvent(self,payload,eventtime=""):
        # Method to immediately send an event to the http event collector

        # If eventtime in epoch not passed as optional argument use current system time in epoch
        if not eventtime:
            eventtime = str(int(time.time()))

        # Fill in local hostname if not manually populated
        if 'host' not in payload:
            payload.update({"host":self.host})

        # Update time value on payload if need to use system time
#        data = {"time":eventtime}
#        data.update(payload)

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # send event to http event collector
        request = urllib2.Request(self.server_uri, context=ctx)
        request.add_header("Authorization", "Splunk " + self.token)
        request.add_data(json.dumps(data))    
        response = urllib2.urlopen(request)
        r = json.loads(response.read())
        if not r["text"] == "Success":
            print response.read()
        
    def batchEvent(self,payload,eventtime=""):
        # Method to store the event in a batch to flush later

        # Fill in local hostname if not manually populated
        if 'host' not in payload:
            payload.update({"host":self.host})

        payloadLength = len(json.dumps(payload))

        if (self.currentByteLength+payloadLength) > self.maxByteLength:
            self.flushBatch()
            # Print debug info if flag set
            if http_event_collector_debug:
                print "auto flushing"
        else:
            self.currentByteLength=self.currentByteLength+payloadLength

        # If eventtime in epoch not passed as optional argument use current system time in epoch
        if not eventtime:
            eventtime = str(int(time.time()))

        # Update time value on payload if need to use system time
        data = {"time":eventtime}
        data.update(payload)
        self.batchEvents.append(json.dumps(data))

    def flushBatch(self):
        # Method to flush the batch list of events

        if len(self.batchEvents) > 0:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            request = urllib2.Request(self.server_uri)
            request.add_header("Authorization", "Splunk " + self.token)
            request.add_data(" ".join(self.batchEvents))    
            response = urllib2.urlopen(request, context=ctx)    
            print response.read()
        
        self.batchEvents = []
        self.currentByteLength = 0
