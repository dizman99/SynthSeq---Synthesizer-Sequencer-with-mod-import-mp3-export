# SynthSeq

**A polyphonic Python synthesizer, sampler and tracker-style sequencer with a built-in MOD importer and WAV/MP3 export.**

> **Note:** This program was written entirely by Perplexity AI. I only accompanied the development: I came up with
> ideas, made the design decisions and tested every single change. Every line of code, however, comes from
> Perplexity AI. Credit where credit is due. This is meant to be a community project. I suspect I won't have much, or
> any, time to add new features or fix bugs. Feel free to add your own changes, fixes, or new gadgets.
>
> My development goal: pure fun. This started as a small Python synthesizer experiment and grew, step by step, into
> a sampler with an effects chain, a sound bank, a 32-track tracker-style sequencer, an importer for classic
> Amiga MOD files and an exporter for WAV and MP3. The result seemed like it might bring joy to other people as
> well, so here it is.
>
> I hope you like it,
>
> Dietmar Bos
>
> (Sidenote: This is not a Tracker! It has a similar interface in the Sequencer section, but it is only here to have a
>  base to start with. The Synth, the 'Generator' is another Level. Start there and then incorporate into the Sequencer)
## Screenshots

**Generator** – oscillators or samples, envelope, unison, loop points and a chain of 13 effects:

![Generator](1.png)

**SoundBank** – patches as tiles, banks, categories, search and sort:

![SoundBank](2.png)

**Sequencer** – 32 tracks, patterns, song order, effect commands:

![Sequencer](3.png)

**MOD import** – a classic tracker module converted into samples, patches, a sound bank and a ready-to-play song, including the import log:

![Sequencer after a MOD import](4.png)

## Features

**Generator (synthesizer / sampler)**
- Polyphonic playback from the computer keyboard (piano layout over two rows, octaves F1–F5).
- Sources: sine, square, sawtooth (band-limited wavetables) and noise – or any WAV file as a sample.
- Sample mode with root note, loop start / end, loop crossfade and zero-crossing snap; WAVs are trimmed of leading and trailing silence on load.
- Unison (1–9 voices) with detune for supersaw-style sounds.
- ADSR envelope and volume.
- Chain of 13 effects: Vocoder, Filter (LP/HP/BP with LFO), Offset-LFO, Overdrive, Fuzz, Distortion, Bitcrusher, Flanger, Chorus, Phaser, Tremolo, Echo and Reverb.
- Vocoder with your own modulator WAV (speech, for example).
- Recording of the synthesizer output, offline rendering of the effect chain into a sample ("Effekte anwenden").
- Patches are stored as readable JSON files.

**SoundBank**
- Banks are ordered lists of patches shown as tiles, with category, per-patch start octave, search, filter and sort.
- Edit a patch directly from the bank (double click) and write it back (a `.bak` backup is made).
- All paths are relative, so the whole program folder can be moved.

**Sequencer**
- 32 tracks, each with one patch, octave, pan and mute; solo for the current session.
- Pattern grid with four fields per track: note, volume (00–99), effect code and effect value.
- Effect commands: pan, switching any of the 13 effects on or off, and setting every effect parameter (smooth 20 ms glide).
- Song order, copy / cut / paste, mouse selection and drag, undo / redo.
- Live recording from the keyboard with count-in and latency correction, metronome, pattern or song playback with loop.

**MOD import**
- Loads Amiga tracker modules (`.mod`, 4 to 8 channels) with one click: samples become WAV files, patches and a sound bank, the patterns become a song with the same tempo and structure.
- Writes a readable import log that lists everything that could not be converted.
- Meant as a *foundation* for your own work: re-sound the instruments with the synth, add effects, rearrange.

**Export**
- "WAV/MP3 speichern" renders the current song offline (faster than real time) and writes `EXPORT/<song>.wav` and `EXPORT/<song>.mp3`.

## Requirements

- Python 3.10 or newer (developed with the standard Windows installer; Tkinter is included).
- Packages: `numpy`, `sounddevice`
- Optional: `lameenc` for MP3 export (without it, only WAV is written)

```
pip install numpy sounddevice
pip install lameenc      # optional, MP3 export
```

Developed and tested on Windows 10/11. Other platforms have not been tested; contributions are welcome.

## Getting started

```
python main.py
```

Next to the program the following folders are created automatically:

| Folder | Content |
|---|---|
| `WAV` | samples and recordings (the MOD importer creates a subfolder per module) |
| `PATCHES` | patch files (`.json`) |
| `SOUNDBANK` | bank files (`.json`) |
| `SEQUENCER` | songs (`.json`) and import logs |
| `EXPORT` | exported WAV / MP3 files |

Play the keys `Q W E R T Z U I O P Ü` (white keys) and `2 3 5 6 7 9 0` (black keys), change the octave with `F1`–`F5`.
A detailed description of every part is in [`Instructions.txt`](Instructions.txt).

## Most asked questions

**How do I get a "talking robot" with the vocoder?**
1. In the *Generator* tab choose the waveform **Sawtooth** (the carrier) and play a low octave (for example `F2`). A single voice (Unisono 1) gives the clearest robot voice.
2. Click **"Modulator laden…"** and load a WAV file with spoken text (a few seconds, one voice, little background noise).
3. Switch the **Vocoder** effect on (checkbox in its tile).
4. Hold a key – the speech is imposed on the pitch you play.
5. Fine-tune: *Glättung* low (about 0.1) = sharp and robotic, high = soft; *Zischlaute* (about 0.3–0.6) brings back the "s" sounds; *Pegel* sets the loudness.
6. For a choir-like sound use Unisono 7 with a higher *Glättung* and add some Reverb. Save the result with **"Patch speichern…"** – the modulator file is stored in the patch.

**Why do my imported MOD songs sound a bit different from the original?**
The importer deliberately keeps things simple: tracker effects (slides, vibrato, arpeggio, volume slides) are not converted, the tempo is a single fixed BPM and the instruments are 8-bit samples played by a modern engine. The import log shows exactly what was dropped. Treat the result as a starting point for your own arrangement.

**An imported sample loops with a click. What can I do?**
Open the patch in the Generator (double click its tile in the SoundBank), move *Loop-Start* / *Loop-Ende*, raise *Crossfade* or enable *Nulldurchgang*, then use **"Zurück in die Bank"** to save it.

**Where is my MP3?**
In the `EXPORT` folder, named after the song. If only the WAV file appears, install the optional package: `pip install lameenc`.

**The keys do not play any more.**
The keyboard focus is probably in a text field (for example the search box). Press `Return` or `Esc`, or click on an empty area of the window.

**Can I move or rename files?**
Keep the folder structure (`WAV`, `PATCHES`, `SOUNDBANK`, `SEQUENCER`). Patches refer to their WAV files and banks / songs refer to patches by relative path; if a file is missing, the SoundBank shows a grey tile or a "WAV fehlt" hint.

**Can I add more effects or more tracks?**
More tracks: change `NUM_TRACKS` in `config.py`. New effects: add the class to `effects.py` and append it **at the end** of `make_chain()` so that existing effect command codes do not shift.

## Project structure

| File | Purpose |
|---|---|
| `main.py`, `app.py` | start-up and the Generator tab |
| `engine.py`, `effects.py` | sound generation, voices, effect chain |
| `mixer.py` | audio output, panning, recording |
| `soundbank.py` | SoundBank tab, bank files |
| `sequencer.py`, `patterngrid.py`, `patternedit.py`, `player.py`, `fxcmd.py` | sequencer, pattern grid, editing rules, playback, effect commands |
| `patch.py`, `patchload.py`, `audio_utils.py`, `config.py`, `widgets.py` | patch format, loading, WAV helpers, settings, knob widget |
| `modimport.py`, `modsong.py` | MOD parser and converter (also usable from the command line) |
| `songexport.py` | offline rendering to WAV and MP3 |

```
python modsong.py my_module.mod -o import_log.txt     # complete import without the GUI
```

## Contributing

Pull requests, bug reports and ideas are welcome. Possible next steps: tracker effects such as portamento, vibrato and arpeggio, more import options, other operating systems, an English user interface (the interface is currently German).

## License

MIT