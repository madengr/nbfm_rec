#!/usr/bin/env python
"""
Created on Sat Mar 22 17:53:33 2014

@author: madengr
"""
#
# nbfm_rec.py for GNU Radio 3.7
# Louis Brown, KD4HSO
# 6/10/2014
#
# Program to monitor and record multiple NBFM channels.
# Program has been tested on the Ettus N210 + WBX, and Ettus B200.
# Channel list is read from newline delimited text file.
# Channels must fall within sampling rate of USRP.
# USRP samples are streamed to N parallel demodulator chains.
# Each chain begins with a frequency translating FIR filter.
# Filter taps are designed for 8 kHz cutoff with 2 kHz transition width.
# Filter decimates to 20 kHz channel sample rate.
# Non-gated power squelch ensures NBFM demod is silent during carrier absence.
# Non-gating is neccesary since samples must always flow to audio sink.
# The NBFM receiver demodulates the signal.
# Gated power squelch halts samples to wav file sink (removes audio gaps).
# Rational resample from 20 kHz channel bandwidth to 8 kHz wav file.
# Sink wave file (named per channel)
# Demodulated channel samples are added together from N chains.
# Rational resample from 20 kHz channel bandwidth to sound card rate.
# Sink to sound card.
# The following graphic (created with asciio) details the flow-graph.
# There are N parallel chains numbered 0 thru N-1.
# Each numbered block is an N length list of objects.
# This coding style makes it very easy to create N identical, parallel chains.
# 8 channels across a 25 MHz bandwidth can be decode on an 3 GHz I7 with VOLK
# An aU is an audio under-run dispalyed on the console.
# Decreasing the RF bandwidth will allow more channels.
#|
#|         .-------.   .--------.   .------.   .-------.   .-------.   .------.
#| .----.  . [0]   .   . [0]    .   . [0]  .   . [0]   .   . [0]   .   . [0]  .
#| |USRP-->. Xlate .-->. NG Pwr .-->. NBFM .-->. G Pwr .-->. Ratnl .-->. Wav  .
#| '----'  . FIR   .   . Sqlch  .   . RX   .   . Sqlch .   . Rsmp  .   . File .
#|    *    '-------'   '--------'   '------'   '-------'   '-------'   '------'
#|    *                                 |
#|    *                                 |
#|    *                                 v        .--------.    .--------.
#|    *                             .-------.    . Ratnl  .    . Audio  .
#|    *                             . Adder .--->. Resamp .--->. Sink   .
#|    *                             '-------'    '--------'    '--------'
#|    *                                 ^
#|    *                                 *
#|    *                                 *
#|    *    .-------.   .--------.   .-------.  .-------.   .-------.   .-------.
#|    *    . [N-1] .   . [N-1]  .   . [N-1] .  . [N-1] .   . [N-1] .   . [N-1] .
#|    ****>. Xlate .-->. NG Pwr .-->. NBFM  .->. G Pwr .-->. Ratnl .-->. Wav   .
#|         . FIR   .   . Sqlch  .   . RX    .  . Sqlch .   . Rsmp  .   . File  .
#|         '-------'   '--------'   '-------'  '-------'   '-------'   '-------'
#|

import __builtin__ # Needed since gr filter and built in filter() conflict
from gnuradio import gr
from gnuradio import uhd
from gnuradio import blocks
from gnuradio import filter
from gnuradio import analog
from gnuradio import audio
from gnuradio.eng_option import eng_option
from optparse import OptionParser
import math
import sys

class MyTopBlock(gr.top_block):

    """ Multi-channel NBFM recorder """

    # Method to initialize the class
    def __init__(self):

        # Call the initialization method from the parent class
        gr.top_block.__init__(self)

        # Setup the parser for command line arguments
        parser = OptionParser(option_class=eng_option)
        parser.add_option("-v", "--verbose", action="store_true",
                          dest="verbose", default=False,
                          help="print settings to stdout")
        parser.add_option("-a", "--args", type="string", dest="src_args",
                          default='addr=192.168.1.13',
                          help="USRP device address args")
        parser.add_option("-g", "--gain", type="eng_float", dest="src_gain",
                          default=0, help="USRP gain in dB")
        parser.add_option("-q", "--squelch", type="eng_float",
                          dest="squelch_thresh", default=-80,
                          help="Squelch threshold in dB")
        parser.add_option("-s", "--soundrate", type="eng_float",
                          dest="snd_card_rate", default=48000,
                          help="Sound card rate in Hz (must be n*100 Hz)")
        parser.add_option("-c", "--channels", type="string",
                          dest="channel_file_name",
                          default='channels.txt',
                          help="Text file of EOL delimited channels in Hz")

        (options, args) = parser.parse_args()
        if len(args) != 0:
            parser.print_help()
            raise SystemExit, 1

        # Define the user constants
        src_args = str(options.src_args)
        src_gain = float(options.src_gain)
        squelch_thresh = float(options.squelch_thresh)
        snd_card_rate = int(options.snd_card_rate)
        channel_file_name = str(options.channel_file_name)

        # Define other constants (don't mess with these)
        max_rf_bandwidth = 25E6 # Limited by N210
        channel_sample_rate = 20000
        nbfm_maxdev = 2.5E3
        nbfm_tau = 75E-6

        # Open file, split to list, remove empty strings, and convert to float
        with open(channel_file_name) as chanfile:
            lines = chanfile.read().splitlines()
        chanfile.close()
        lines = __builtin__.filter(None, lines)
        chanlist = [float(chan) for chan in lines]

        # Source decimation is first deternmined by the required RF bandwidth
        rf_bandwidth = max(chanlist) - min(chanlist) + 2*channel_sample_rate
        src_decimation = int(math.floor(max_rf_bandwidth/rf_bandwidth))

        # Check if rf_bandwidth is too wide
        if rf_bandwidth > max_rf_bandwidth:
            print 'Error: Channels spaced beyond the \
                %f MHz maximum RF bandwidth!' % (max_rf_bandwidth/1E6)
            sys.exit([1])
        else:
            pass

        # Don't let the source decimation go above 100 (USRP N210 limit)
        if src_decimation > 100:
            src_decimation = 100

        # This is a little tricky
        # Don't want odd values of source decimation greater than 1
        # Also want the source sample rate \
        # to be an integer multiple of channel sample rate
        src_sample_rate = max_rf_bandwidth / src_decimation
        while ((src_decimation%2 != 0) or \
            ((max_rf_bandwidth/src_decimation) % channel_sample_rate != 0)) \
            and src_decimation > 1:
            src_decimation = src_decimation - 1
            src_sample_rate = max_rf_bandwidth / src_decimation

        # Calculate the channel decimation for the fxlating filter
        # (it will be an integer)
        channel_decimation = int(src_sample_rate / channel_sample_rate)

        # Calculate center frequency
        src_center_freq = (max(chanlist) + min(chanlist)) / 2

        # Print some info to stdout for verbose option
        if options.verbose:
            print 'Source args string "%s" ' % src_args
            print 'Source center frequency = %f MHz' % (src_center_freq/1E6)
            print 'Source decimation = %i' % src_decimation
            print 'Source sample rate = %i Hz' % src_sample_rate
            print 'Source gain = %i dB' % src_gain
            print 'Squelch threshold = %i dB' % squelch_thresh
            print 'Channel decimation = %i' % channel_decimation
            print 'Channel sample rate = %i Hz' % channel_sample_rate
            print 'Sound card rate = %i Hz' % snd_card_rate
            print 'Channel list = %s' % str(chanlist)

        # Setup the source
        src = uhd.usrp_source(src_args, uhd.io_type_t.COMPLEX_FLOAT32, 1)
        src.set_samp_rate(src_sample_rate)
        src.set_center_freq(src_center_freq, 0)
        src.set_gain(src_gain, 0)

        # Get USRP true center frequency
        # Do nothing with it as it's only a few Hz error
        #print src.get_center_freq()

        # Create N channel flows---------------------------------------------

        # Design taps for frequency translating FIR filter
        filter_taps = filter.firdes.low_pass(1.0,
                                             src_sample_rate,
                                             8E3,
                                             2E3,
                                             filter.firdes.WIN_HAMMING)

        # N parallel fxlating FIR filter with decimation to channel rate
        # Note how the tune freq is chan-src_center_freq ; reversed from GR 3.6
        fxlate = [filter.freq_xlating_fir_filter_ccc(channel_decimation,
                                                 filter_taps,
                                                 chan - src_center_freq,
                                                 src_sample_rate)
                  for chan in chanlist]

        # Power squelch (complex, non blocking) prior to NBFM
        squelch1 = [analog.pwr_squelch_cc(squelch_thresh,
                                          0.1,
                                          1,
                                          False) for chan in chanlist]

        # NBFM receiver
        nbfm = [analog.nbfm_rx(channel_sample_rate,
                               channel_sample_rate,
                               nbfm_tau,
                               nbfm_maxdev) for chan in chanlist]

        # Power squelch (float, blocking) prior to wav file resampling
        squelch2 = [analog.pwr_squelch_ff(squelch_thresh,
                                          0.1,
                                          1,
                                          True) for chan in chanlist]

        # Rational resampler for channel rate to 8 kHz wav file rate
        resampwav = [filter.rational_resampler_fff(8000,
                                                   int(channel_sample_rate))
                                                   for chan in chanlist]

        # Wav file sink
        wavfile = [blocks.wavfile_sink(str(int(chan))+'.wav',
                                       1,
                                       8000,
                                       8) for chan in chanlist]

        # Connect the blocks
        for chan in range(len(chanlist)):
            self.connect(src, fxlate[chan], squelch1[chan], nbfm[chan],
                         squelch2[chan], resampwav[chan], wavfile[chan])

        # Adder to sum the nbfm outputs for sound card
        adder = blocks.add_vff(1)

        # Rational resampler for channel rate to audio rate
        resampsc = filter.rational_resampler_fff(int(snd_card_rate),
                                                 int(channel_sample_rate))

        # Sound card sink
        sndcard = audio.sink(snd_card_rate, "", True)

        # Connect the blocks
        for chan in range(len(chanlist)):
            self.connect(nbfm[chan], (adder, chan))

        # Connect the blocks
        self.connect(adder, resampsc, sndcard)


if __name__ == '__main__':
    try:
        MyTopBlock().run()
    except KeyboardInterrupt:
        pass
