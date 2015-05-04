import sys
import os
import getopt
import subprocess
import re
import time
import csv
from lxml import etree
import numpy as np
from scipy.misc import imread
from tifffile import imsave
from scipy.ndimage.measurements import histogram
from itertools import  combinations
from itertools import groupby
from collections import OrderedDict
from collections import defaultdict
from control_class import Base
from control_class import Directory
from control_class import File
from socket_client import Client

def usage():
    """Usage function to help user start the script"""

    print("""Usage: """+sys.argv[0]+""" -i <dir> [options]

    Options and arguments:
    -h, --help                  : show the usage information
    -i <dir>, --input=<dir>     : set imaging directory
    --wdir=<dir>                : set working directory
    --std=<well>                : set standard well
    --firstgain=<gain_file>     : set first initial gains file
    --secondgain=<gain_file>    : set second initial gains file
    --finwell=<well>            : set final well
    --finfield=<field>          : set final field
    --coords=<file>             : set 63x coordinates file
    --host=<ip>                 : set host ip address""")

def camstart_com(_afjob, _afr, _afs):
    """Returns a cam command to start the cam scan with selected AF job
    and AF settings."""

    _com = ('/cli:1 /app:matrix /cmd:startcamscan /runtime:36000'
            ' /repeattime:36000 /afj:'+_afjob+' /afr:'+_afr+' /afs:'+_afs)
    return _com

def gain_com(_job, _pmt, _gain):
    """Returns a cam command for changing the pmt gain in a job."""

    _com = ('/cli:1 /app:matrix /cmd:adjust /tar:pmt /num:'+_pmt+
            ' /exp:'+_job+' /prop:gain /value:'+_gain
            )
    return _com

def get_wfx(_compartment):
    """Returns a string representing the well or field X coordinate."""

    return str(int(re.sub(r'\D', '', re.sub('--.\d\d', '', _compartment)))+1)

def get_wfy(_compartment):
    """Returns a string representing the well or field Y coordinate."""

    return str(int(re.sub(r'\D', '', re.sub('.\d\d--', '', _compartment)))+1)

def enable_com(_well, _field, enable):
    """Returns a cam command to enable a field in a well."""

    wellx = get_wfx(_well)
    welly = get_wfy(_well)
    fieldx = get_wfx(_field)
    fieldy = get_wfy(_field)
    _com = ('/cli:1 /app:matrix /cmd:enable /slide:0 /wellx:'+wellx+
            ' /welly:'+welly+' /fieldx:'+fieldx+' /fieldy:'+fieldy+
            ' /value:'+enable)
    return _com

def cam_com(_job, _well, _field, _dx, _dy):
    """Returns a cam command to add a field to the cam list."""

    _wellx = get_wfx(_well)
    _welly = get_wfy(_well)
    _fieldx = get_wfx(_field)
    _fieldy = get_wfy(_field)
    _com = ('/cli:1 /app:matrix /cmd:add /tar:camlist /exp:'+_job+
            ' /ext:af /slide:0 /wellx:'+_wellx+' /welly:'+_welly+
            ' /fieldx:'+_fieldx+' /fieldy:'+_fieldy+' /dxpos:'+_dx+
            ' /dypos:'+_dy
            )
    return _com

def process_output(_well, output, dl):
    """Function to process output from the R scripts."""

    dl.append({'well': _well,
              'green': output.split()[0],
              'blue': output.split()[1],
              'yellow': output.split()[2],
              'red': output.split()[3]
              })
    return dl

def write_csv(path, dict_list, keys):
    """Function to write a list of dicts as a csv file."""

    with open(path, 'wb') as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        w.writerows(dict_list)

def make_proj(img_list):
    """Function to make a dict of max projections from a list of paths
    to images. Each channel will make one max projection"""
    channels = []
    for path in img_list:
        channel = File(path).get_name('C\d\d')
        channels.append(channel)
        channels = sorted(set(channels))
    max_imgs = {}
    for channel in channels:
        images = []
        for path in img_list:
            if channel == File(path).get_name('C\d\d'):
                images.append(imread(path))
        max_imgs[channel] = np.maximum.reduce(images)
    return max_imgs

def main(argv):
    """Main function"""

    try:
        opts, args = getopt.getopt(argv, 'hi:', ['help',
                                                 'input=',
                                                 'wdir=',
                                                 'std=',
                                                 'firstgain=',
                                                 'secondgain=',
                                                 'finwell=',
                                                 'finfield=',
                                                 'coords=',
                                                 'host='
                                                 ])
    except getopt.GetoptError as e:
        print e
        usage()
        sys.exit(2)

    if not opts:
        usage()
        sys.exit(0)

    imaging_dir = ''
    working_dir = os.path.dirname(os.path.abspath(__file__))
    std_well = 'U00--V00'
    first_initialgains_file = os.path.normpath(working_dir+'/10x_gain.csv')
    sec_initialgains_file = os.path.normpath(working_dir+'/40x_gain.csv')
    last_well = 'U00--V00'
    last_field = 'X01--Y01'
    coord_file = None
    host = ''
    for opt, arg in opts:
        if opt in ('-h', '--help'):
            usage()
            sys.exit()
        elif opt in ('-i', '--input'):
            imaging_dir = os.path.normpath(arg)
        elif opt in ('--wdir'):
            working_dir = os.path.normpath(arg)
        elif opt in ('--std'):
            std_well = arg # 'U00--V00'
        elif opt in ('--firstgain'):
            first_initialgains_file = os.path.normpath(arg)
        elif opt in ('--secondgain'):
            sec_initialgains_file = os.path.normpath(arg)
        elif opt in ('--finwell'):
            last_well = arg # 'U00--V00'
        elif opt in ('--finfield'):
            last_field = arg # 'X00--Y00'
        elif opt in ('--coords'):
            coord_file = os.path.normpath(arg) #
        elif opt in ('--host'):
            host = arg
        else:
            assert False, 'Unhandled option!'

    # Paths
    first_r_script = os.path.normpath(working_dir+'/gain.r')
    sec_r_script = os.path.normpath(working_dir+'/gain_change_objectives.r')

    # Job names
    af_job_10x = 'af10xcam'
    afr_10x = '200'
    afs_10x = '41'
    af_job_40x = 'af40x'
    afr_40x = '105'
    afs_40x = '106'
    af_job_63x = 'af63x'
    afr_63x = '50'
    afs_63x = '51'
    g_job_10x = 'gain10x'
    g_job_40x = 'gain40x'
    g_job_63x = 'gain63x'
    job_40x = ['job7', 'job8', 'job9']
    pattern_40x = 'pattern2'
    job_63x = ['job10', 'job11', 'job12', 'job13', 'job14', 'job15',
               'job16', 'job17', 'job18', 'job19', 'job20', 'job21']
    pattern_63x = ['pattern3', 'pattern4', 'pattern5', 'pattern6']
    job_dummy = 'job22'

    # Booleans to control flow.
    stage0 = True
    stage1 = True
    stage1after = False
    stage2before = True
    stage2after = False
    stage3 = True
    stage4 = False
    stage5 = False
    if coord_file:
        stage2before = False
        stage3 = False
        stage4 = True
        coords = defaultdict(list)
        with open(coord_file) as _file:
            reader = csv.DictReader(_file)
            for d in reader:
                for coord in ['dx', 'dy']:
                    coords[d['fov']].append(d[coord])

    start_com = '/cli:1 /app:matrix /cmd:startscan'
    stop_com = '/cli:1 /app:matrix /cmd:stopscan'

    # Create imaging directory object
    img_dir = Directory(imaging_dir)

    # Create socket
    sock = Client()
    # Port number
    port = 8895
    # Connect to server
    sock.connect(host, port)

    sec_gain_dicts = []
    
    with open(os.path.normpath(working_dir+'/sec_output_gains.csv')) as _file:
        reader = csv.DictReader(_file)
        for d in reader:
            sec_gain_dicts.append(d)

    # Lists for storing command strings.
    com_list = []
    end_com_list = []

    odd_even = 0
    wells = defaultdict()
    gains = defaultdict(list)
    green_sorted = defaultdict(list)
    medians = defaultdict(int)
    com = '/cli:1 /app:matrix /cmd:deletelist'+'\n'
    end_com = ['/cli:1 /app:matrix /cmd:deletelist'+'\n']
    dx = 0
    dy = 0
    pattern = -1
    start_of_part = False
    prev_well = ''

    for c in ['green', 'blue', 'yellow', 'red']:
        mlist = []
        for d in sec_gain_dicts:
            # Sort gain data into a list dict with well as key and where the
            # value is a list with a gain value for each channel.
            gains[d['well']].append(d[c])
            if c == 'green':
                # Round gain values to multiples of 10 in green channel
                d['green'] = int(round(int(d['green']), -1))
                green_sorted[d['green']].append(d['well'])
                well_no = 8*(int(get_wfx(d['well']))-1)+int(get_wfy(d['well']))
                wells[well_no] = d['well']
            else:
                # Find the median value of all gains in
                # blue, yellow and red channels.
                mlist.append(int(d[c]))
                medians[c] = int(np.median(mlist))

    if stage3:
        print('Stage3')
        camstart = camstart_com(af_job_40x, afr_40x, afs_40x)
        stage_dict = green_sorted
        job_list = job_40x
        pattern_list = pattern_40x
        enable = 'true'
    if stage4:
        print('Stage4')
        camstart = camstart_com(af_job_63x, afr_63x, afs_63x)
        channels = range(4)
        wells = OrderedDict(sorted(wells.items(), key=lambda t: t[0]))
        stage_dict = wells
        old_well_no = wells.items()[0][0]-1
        job_list = job_63x
    for k, v in stage_dict.iteritems():
        if stage3:
            channels = [k,
                        medians['blue'],
                        medians['yellow'],
                        medians['red']
                        ]
        if stage4:
            # Check if well no 1-4 or 5-8 etc and continuous.
            if ((round((float(k)+1)/4) % 2 == odd_even) |
                (old_well_no + 1 != k)):
                pattern = 0
                start_of_part = True
                if odd_even == 0:
                    odd_even = 1
                else:
                    odd_even = 0
            else:
                pattern =+ 1
                start_of_part = False
            pattern_list = pattern_63x[pattern]
        if start_of_part:
            # Store the commands in lists, after one well at least.
            com_list.append(com)
            end_com_list.append(end_com)
            com = '/cli:1 /app:matrix /cmd:deletelist'+'\n'
        for i, c in enumerate(channels):
            if stage3:
                set_gain = str(c)
                start_of_part = True
            if stage4:
                set_gain = str(gains[v][i])
            if i < 2:
                detector = '1'
                job = job_list[i]
            if i >= 2:
                detector = '2'
                job = job_list[i-1]
            com = com + gain_com(job, detector, set_gain) + '\n'
        for well in v:
            if stage4:
                well = v
            print(well)
            if well != prev_well:
                prev_well = well
                for i in range(2):
                    for j in range(2):
                        if stage4:
                            # Only enable selected wells from file (arg)
                            fov = well+'--X0'+str(j)+'--Y0'+str(i)
                            if fov in coords.keys():
                                enable = 'true'
                                dx = coords[fov][0]
                                dy = coords[fov][1]
                            else:
                                enable = 'false'
                        com = (com +
                                   enable_com(well,
                                              'X0'+str(j)+'--Y0'+str(i),
                                              enable
                                              )+
                                   '\n'+
                                   cam_com(pattern_list,
                                           well,
                                           'X0'+str(j)+'--Y0'+str(i),
                                           str(dx),
                                           str(dy)
                                           )+
                                   '\n')
                        end_com = ['CAM',
                                    well,
                                    'E03',
                                    'X0'+str(j)+'--Y0'+str(i)
                                    ]

    # Store the last unstored commands in lists, after one well at least.
    com_list.append(com)
    end_com_list.append(end_com)

    for i, com in enumerate(com_list):
        # Stop scan
        print(stop_com)
        sock.send(stop_com)
        time.sleep(3)
        # Send gain change command to server in the four channels.
        # Send CAM list to server.
        print(com)
        sock.send(com)
        time.sleep(3)
        # Start scan.
        print(start_com)
        sock.send(start_com)
        time.sleep(3)
        # Start CAM scan.
        print(camstart)
        sock.send(camstart)
        time.sleep(3)
        if stage3:
            sock.recv_timeout(40, end_com_list[i])
        if stage4:
            stage5 = True
        while stage5:
            metadata_d = {}
            reply = sock.recv_timeout(40, ['E03'])
            # parse reply, check well (UV), job-order (E), field (XY),
            # z slice (Z) and channel (C). Get well path.
            # Get all image paths in well. Rename images.
            # Make a max proj per channel. Save meta data and image max proj.
            img_name = File(reply).get_name('image--.*')
            img_paths = img_dir.get_all_files(img_name)
            field_path = File(img_paths[0]).get_dir()
            well_path = Directory(field_path).get_dir()
            img_paths = Directory(field_path).get_all_files('*.tif')
            new_paths = []
            for img_path in img_paths:
                img = File(img_path)
                well = img.get_name('U\d\d--V\d\d')
                job_order = img.get_name('E\d\d')
                field = img.get_name('X\d\d--Y\d\d')
                z_slice = img.get_name('Z\d\d')
                channel = img.get_name('C\d\d')
                if job_order == 'E01':
                    new_name = img_path[0:-11]+'C00.ome.tif'
                if job_order == 'E02' and channel == 'C00':
                    new_name = img_path[0:-11]+'C01.ome.tif'
                if job_order == 'E02' and channel == 'C01':
                    new_name = img_path[0:-11]+'C02.ome.tif'
                if job_order == 'E03':
                    new_name = img_path[0:-11]+'C03.ome.tif'
                os.rename(img_path, new_name)
                if (job_order == 'E01' or job_order == 'E02' or
                    job_order == 'E03'):
                    new_paths.append(new_name)
                    metadata_d[well+'--'+field+'--'+channel] = img.meta_data()

            max_projs = make_proj(new_paths)
            new_dir = imaging_dir+'/maxprojs/'
            if not os.path.exists(new_dir): os.makedirs(new_dir)
            for channel, proj in max_projs.iteritems():
                p = new_dir+well+'--'+field+'--'+channel+'.tif'
                metadata = metadata_d[well+'--'+field+'--'+channel]
                imsave(p, proj, description=metadata)
            if all(test in reply for test in end_com_list[i]):
                stage5 = False
        time.sleep(3)
        # Stop scan
        print(stop_com)
        sock.send(stop_com)
        time.sleep(5)

if __name__ =='__main__':
    main(sys.argv[1:])