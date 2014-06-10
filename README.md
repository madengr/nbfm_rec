nbfm_rec.py is a multi-channel narrow band FM recorder for GNU Radio

Tested with GNU Radio 3.7, Ettus N210 + WBX, and B200

Run with -h for help

Channels (in Hz) are read from channels.txt newline delimited file
Suggest starting with 162.55E6 for local NOAA radio or 144.39E6 for APRS

Keep adding channels as long as they are within the 25 MHz sample rate
supported by the USRP, and your processor can handle the load.
Running volk_profile will make a big difference

Use with the Ettus N210 + WBX:
./nbfm_rec.py --args="addr=168.1.13" --gain=10 -v

Use with the Ettus B200:
Note the program was developed with the N210 which has a maximum sample rate
of 25 MHz, therefore the B200 master clock should be set to 25E6:
./nbfm_rec.py --args="type=b200, master_clock_rate=25E6" --gain=50 -v

nbfm_rec_diagram.asciio is the ASCIIO block diagram