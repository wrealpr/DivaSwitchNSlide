## Problems
My PD Launcher now hangs no matter what settings are changed unless COM12 is held open. Hence the inclusion of `keepalive.py` in all releases. Although this isn't really a fix because slider inputs coming from keyboard assignments in `keyconfig.ini` don't work from here on out. 

Also worth noting that the Switch app is in very poor condition. Most of the parsing input comes from the script I believe. (vibecoded slop IDK) so if you know how, you can probably fix up the app's various issues. Most notable problems are that the [+] to quit only works when the script is running on PC and the screen should/could dim but doesn't.

# DivaSwitchNSlide

""Native"" integration via the `.dva` plugin is not as streamlined as it could be and is not recommended.

 Main release is a Python script that polls the Nintendo Switch app with pyusb before sending back touch data. Data is then sent over a virtual COM port provided by [com0com](https://com0com.sourceforge.net/)

## Usage

1. Install [com0com](https://com0com.sourceforge.net/) and change the default numbers on Virtual Port Pair 1 to `COM11` and `COM12` 

2. Install `pip install pyusb` and [Zadig](https://zadig.akeo.ie) or `winget install --id akeo.ie.Zadig -e`

3. Copy touch_capture.nro onto `SD Card\Switch`

4. Open `PD Loader\plugins\config.ini` or Navigate to the "Options" tab in the launcher and either set `Hardware_Slider = 1` or tick the box that says "Use hardware slider"

5. Make sure Nintendo Switch is plugged in with .nro open beforehand, then run `switch_slider_bridge.py`


#### Thanks
dogtopus for the serial slider information at https://gist.github.com/dogtopus/b61992cfc383434deac5fab11a458597
