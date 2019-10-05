#!/usr/bin/python
# -*- coding: utf-8 -*-

import os
import subprocess
import json
import re
import time
import datetime

from shapely.geometry import Point, mapping
from hachoir_core.error import HachoirError
from hachoir_core.cmd_line import unicodeFilename
from hachoir_parser import createParser
from hachoir_core.tools import makePrintable
from hachoir_metadata import extractMetadata
from hachoir_core.i18n import getTerminalCharset
import geocoder
import exifread as ef
import mmap
import filecmp


def _convert_to_degress(value):
    d = float(value.values[0].num) / float(value.values[0].den)
    m = float(value.values[1].num) / float(value.values[1].den)
    s = float(value.values[2].num) / float(value.values[2].den)

    return d + (m / 60.0) + (s / 3600.0)


def getGPS(filepath):
    with open(filepath, 'r') as f:
        tags = ef.process_file(f)
        if tags.get('GPS GPSLatitude') is not None:
            latitude = tags.get('GPS GPSLatitude')
            latitude_ref = tags.get('GPS GPSLatitudeRef')
            longitude = tags.get('GPS GPSLongitude')
            longitude_ref = tags.get('GPS GPSLongitudeRef')

            if latitude:
                lat_value = _convert_to_degress(latitude)
                if latitude_ref.values != 'N':
                    lat_value = -lat_value
            else:
                return {}
            if longitude:
                lon_value = _convert_to_degress(longitude)
                if longitude_ref.values != 'E':
                    lon_value = -lon_value
            else:
                return {}
        else:
            lat_value = 'none'
            lon_value = 'none'

        if tags.get('Image DateTime') is not None:
            year = tags.get('Image DateTime').values.split(" ")[0].split(":")[0]
        elif tags.get('Image Software') is not None and tags.get('Image Software').values == 'Instagram':
            year = 'Instagram'
        else:
            year = 'none'
        if tags.get('EXIF DateTimeOriginal') is not None:
            DateTime = tags.get('EXIF DateTimeOriginal').values
        else:
            DateTime = 'none'
        if tags.get('Image Model') is not None:
            Device = tags.get('Image Model').values
        else:
            Device = 'none'

    return {'latitude': lat_value, 'longitude': lon_value, 'date': year, 'DateTime': DateTime, 'Device': Device}


def geo_data(lat,lon):
    try:
        geocoded = geocoder.mapquest([lat, lon], method='reverse', key=os.environ['MAPQUEST_KEY'])
        geolist = (geocoded.country, geocoded.city, geocoded.json['raw']['street'], geocoded.postal)
        return geolist
    except Exception as e:
        print e


# Get metadata for video file
def metadata_for(filename):

    filename, realname = unicodeFilename(filename), filename
    parser = createParser(filename, realname)
    if not parser:
        print "Unable to parse file"
        exit(1)
    try:
        metadata = extractMetadata(parser)
    except HachoirError, err:
        print "Metadata extraction error: %s" % unicode(err)
        metadata = None
    if not metadata:
        print "Unable to extract metadata"
        exit(1)

    text = metadata.exportPlaintext()
    charset = getTerminalCharset()
    for line in text:
        print makePrintable(line, charset)

    return metadata


def movgps(filename):
    pattern = re.compile('[-+][0-9.]+[.][0-9.]+[-+][0-9.]+[.][0-9.]+')
    with open(filename, "r+b") as f:
        try:
            mm = mmap.mmap(f.fileno(), 0)
        except OSError as e:
            print("OSError({0}): {1}".format(e.errno, e.strerror))
            print("The 32bit Python cannot handle huge .mov file")
            print("Please install the 64bit Python")
        coordinates = re.findall(pattern, mm)
        if len(coordinates) > 0:
            coordinates = re.findall(pattern, mm)[0]
            lat = float(coordinates[0:8])
            lon = float(coordinates[8:17])
            meta = metadata_for(filename)
            year = meta.getValues('creation_date')[0].strftime("%Y")
            datetime = meta.getValues('creation_date')[0].strftime("%Y:%m:%d %H:%M%:%S")

            return {'latitude':lat, 'longitude':lon, 'date':year, 'DateTime':datetime}
        else:
            meta = metadata_for(filename)
            year = meta.getValues('creation_date')[0].strftime("%Y")

            return {'latitude': 'none', 'longitude': 'none', 'date': year}


def filecheck(file1, file2):
    same = filecmp.cmp(file1,file2)
    return same


def timestamp():
    ts = int(time.time())
    st = datetime.datetime.fromtimestamp(ts).strftime('%Y%m%d%H%M%S')
    return st


def build_feature(path, gps):
    point = Point(gps['longitude'], gps['latitude'])
    tags = geo_data(gps['latitude'], gps['longitude'])
    country = tags[0]
    city = tags[1]
    street = re.sub(p, "", tags[2])
    pc = tags[3]
    year = gps['date']
    datetime = gps['DateTime']

    feature = {"type": "Feature", 'geometry': mapping(point), "properties":
                {'name': file, 'country': country, 'city': city, 'street': street, 'postal': pc,
                    'DateTime': datetime, 'Year': year, 'path': path}}
    return feature


def build_item(path, gps, item_type):
    f = build_feature(path, gps)
    if item_type == 'foto':
        f['properties']['Device'] = gps['Device']
    elif item_type == 'mov':
        f['properties']['Device'] = 'iPhoneCamera'


def process(args):

    ofile = args.output.rstrip('/') + 'output.geojson'

    if os.path.isfile(ofile) is True:
        js = open(ofile,'r')
        content = json.load(js)['features']
        js.close()
        j = open(ofile,'w')
    else:
        j = open(ofile, 'w')
        content = []

    p = re.compile('[0-9.]+[a-zA-Z]\s|\s[0-9.]+[a-zA-Z]|[0-9.]+\s|\s[0-9.]+')

    for folder in folders:
        for file in os.listdir(folder):
            print file
            f = open('%s/%s' % (folder, file), 'rb')
            try:
                if len(ef.process_file(f)) > 0: # Foto con EXIF
                    gps = getGPS('%s/%s' % (folder, file))
                    print str(gps['latitude']) + " " + str(gps['longitude'])
                    if gps['latitude'] != 'none' and gps['longitude'] != 'none': #Foto con EXIF y GPS
                        point = Point(gps['longitude'], gps['latitude'])
                        tags = geo_data(gps['latitude'], gps['longitude'])
                        country = tags[0]
                        city = tags[1]
                        street = re.sub(p, "", tags[2])
                        pc = tags[3]
                        finalf = street + "-" + pc
                        year = gps['date']
                        datetime = gps['DateTime']
                        device = gps['Device']

                        inpath = folder + "/" + file
                        outpath = out_folder + country + "/" + city + "/" + finalf + "/" + year + "/" + file

                        if os.path.isfile(outpath) is False: # Archivo no existe

                            feature = {"type": "Feature", 'geometry': mapping(point), "properties":
                                {'name': file, 'country': country, 'city': city, 'street': street, 'postal': pc,
                                'DateTime': datetime, 'Device': device, 'path': inpath}}
                            content.append(feature)
                        else: # Archivo ya existe
                            if filecheck(inpath,outpath) is True: # Es el mismo archivo
                                print "File already in destination folder. Skipping..."
                            else: # Tiene el mismo nombre pero son distintos archivos
                                ts = timestamp()
                                outpath = out_folder + country + "/" + city + "/" + finalf + "/" + year + "/" + file[:-4] \
                                          + "_" + str(ts) + file[-4:]

                                feature = {"type": "Feature", 'geometry': mapping(point), "properties":
                                    {'name': file, 'country': country, 'city': city, 'street': street, 'postal': pc,
                                    'DateTime': datetime, 'Device': device,'path': folder + "/" + file}}
                                content.append(feature)

                else:
                    if file.lower().endswith('.mov'): # Es MOV
                        gps = movgps(folder + "/" + file)
                        if gps['latitude'] != 'none': # Es MOV y tiene GPS
                            point = Point(gps['longitude'], gps['latitude'])
                            tags = geo_data(gps['latitude'], gps['longitude'])
                            country = tags[0]
                            city = tags[1]
                            street = re.sub(p, "", tags[2])
                            pc = tags[3]
                            finalf = street + "-" + pc
                            year = gps['date']
                            datetime = gps['DateTime']

                            inpath = folder + "/" + file
                            outpath = out_folder + country + "/" + city + "/" + finalf + "/" + year + "/" + file

                            if os.path.isfile(outpath) is False:  # Archivo no existe

                                feature = {"type": "Feature", 'geometry': mapping(point), "properties":
                                    {'name': file, 'country': country, 'city': city, 'street': street, 'postal': pc,
                                    'DateTime': datetime, 'Device': 'Unknown','path': folder + "/" + file}}
                                content.append(feature)
                            else:  # Archivo ya existe
                                if filecheck(inpath, outpath) is True:  # Es el mismo archivo
                                    print "File already in destination folder. Skipping..."
                                else:  # Tiene el mismo nombre pero son distintos archivos
                                    ts = timestamp()
                                    outpath = out_folder + country + "/" + city + "/" + finalf + "/" + year + "/" + file[:-4] \
                                            + "_" + str(ts) + file[-4:]

                                    feature = {"type": "Feature", 'geometry': mapping(point), "properties":
                                        {'name': file, 'country': country, 'city': city, 'street': street, 'postal': pc,
                                        'DateTime': datetime, 'Device': 'Unknown','path': folder + "/" + file}}
                                    content.append(feature)

            except Exception as e:
                print e

    json.dump({"type": "FeatureCollection", "features": content}, j)
    j.close()


