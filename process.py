#!/usr/bin/python
# -*- coding: utf-8 -*-

import os
import json
import re
import time
import datetime
import logging as log
import configargparse
from progress.bar import IncrementalBar
from progress.counter import Counter

from shapely.geometry import Point, mapping
from hachoir_core.error import HachoirError
from hachoir_core.cmd_line import unicodeFilename
from hachoir_parser import createParser
from hachoir_metadata import extractMetadata
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
        log.error(e)
        geolist = ('unknown','unknown','unknown','unknown')
        return geolist


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

    # text = metadata.exportPlaintext()
    # charset = getTerminalCharset()
    # for line in text:
    #     print makePrintable(line, charset)

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
    p = re.compile('[0-9.]+[a-zA-Z]\s|\s[0-9.]+[a-zA-Z]|[0-9.]+\s|\s[0-9.]+')
    point = Point(gps['longitude'], gps['latitude'])
    tags = geo_data(gps['latitude'], gps['longitude'])
    country = tags[0]
    city = tags[1]
    street = re.sub(p, "", tags[2])
    pc = tags[3]
    year = gps['date']
    datetime = gps['DateTime']

    feature = {"type": "Feature", 'geometry': mapping(point), "properties":
                {'name': path.split("/")[-1], 'country': country, 'city': city, 'street': street, 'postal': pc,
                    'DateTime': datetime, 'Year': year, 'path': path}}
    return feature


def build_item(path, gps, item_type):
    f = build_feature(path, gps)
    if item_type == 'foto':
        f['properties']['Device'] = gps['Device']
    elif item_type == 'mov':
        f['properties']['Device'] = 'iPhoneCamera'
    return f


def iterate_files(inp):
    for dirpath, dirs, files in os.walk(inp):
        for filename in files:
            fname = os.path.join(dirpath, filename)
            yield fname


def count_files(inp):
    counter = Counter('Loading files tree... ')
    t = 0
    for dirpath, dirs, files in os.walk(inp):
        for filename in files:
            t += 1
            counter.next()
    counter.finish()
    return t


def check_content(cindex, feature):
    try:
        if cindex[feature['properties']['name']] == feature['properties']['DateTime']:
            #log.warning('Duplicated found: %s' % feature['properties']['path'])
            return True
        else:
            return False
    except KeyError:
        return False


def load_cindex(content):
    d = {}
    print('Output file exists. Loading %s features' % len(content))
    for f in content:
        d[f['properties']['name']] = f['properties']['DateTime']
    return d


def process(args):
    log.basicConfig(level=log.ERROR, format='%(levelname)s:%(message)s')

    fl = count_files(args.input)

    fotos_files = 0
    processed = 0
    not_media = 0
    failed = 0
    fotos_not_exif = 0
    mov_not_exif = 0
    mov_files = 0
    total = 0
    copies = 0

    ofile = args.output.rstrip('/') + '/output.geojson'

    if os.path.isfile(ofile):
        try:
            js = open(ofile, 'r')
            content = json.load(js)['features']
            cindex = load_cindex(content)
        except ValueError:
            print('GeoJSON corrupt. Overwritting.')
            j = open(ofile, 'w')
            content = []
            cindex = {}
        else:
            js.close()
            j = open(ofile, 'w')
    else:
        j = open(ofile, 'w')
        content = []
        cindex = {}

    bar = IncrementalBar('Processing', max=fl)
    try:
        for file_name in iterate_files(args.input):
            try:
                f = open(file_name, 'rb')
            except Exception:
                failed +=1
                total +=1
                bar.next()
                continue

            if not file_name.lower().endswith('.mov') and (file_name.startswith(".") or
                                                                   len(ef.process_file(f)) == 0):
                not_media += 1
            else:
                try:
                    f = open(file_name, 'rb')
                    if len(ef.process_file(f)) > 0 and not file_name.lower().endswith('.mov'):
                        gps = getGPS(file_name)
                        if gps['latitude'] != 'none' and gps['longitude'] != 'none':
                            feature = build_item(file_name, gps, 'foto')
                            if not check_content(cindex, feature):
                                content.append(feature)
                                fotos_files += 1
                                processed += 1
                                cindex[feature['properties']['name']] = feature['properties']['DateTime']
                            else:
                                copies += 1
                        else:
                            fotos_not_exif += 1

                    elif file_name.lower().endswith('.mov'):
                        gps = movgps(file_name)
                        if gps['latitude'] != 'none':
                            feature = build_item(file_name, gps, 'mov')
                            if not check_content(cindex, feature):
                                content.append(feature)
                                mov_files += 1
                                processed += 1
                                cindex[feature['properties']['name']] = feature['properties']['DateTime']
                            else:
                                copies += 1
                        else:
                            mov_not_exif += 1

                except Exception as e:
                    log.error(e)
                    failed += 1

            total += 1
            bar.next()

    except KeyboardInterrupt:
        print
        print
        log.error('Interrupted by user. Killed.')

    bar.finish()

    print ('Finished: TOTAL: %s | PROCESSED: %s | FOTOS: %s | MOVs: %s | FOTOS W/O EXIF: %s | MOV W/O EXIF: %s '
             '| NOT MEDIA: %s | FAILED: %s | COPIES: %s' %
             (total, processed, fotos_files, mov_files, fotos_not_exif, mov_not_exif, not_media, failed, copies))

    json.dump({"type": "FeatureCollection", "features": content}, j)
    j.close()


def main():
    parser = configargparse.ArgParser()
    parser.add('input', type=str, metavar='input', help='Path to input directory')
    parser.add('output', type=str, metavar='output', help='Path for output geojson',
               default='.')
    parser.set_defaults(func=process)
    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    main()
