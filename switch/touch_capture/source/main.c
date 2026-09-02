// touch_capture - Switch homebrew app
//
// Polls the touchscreen via the hid service and streams each active
// touch point out over usb:ds (libnx's usbComms channel, the same one
// nxlink uses for debug logging). Meant as milestone 1: prove the data
// path works locally before wrapping this in a background sysmodule.
//
// Build with devkitA64 + libnx (see Makefile). Produces touch_capture.nro,
// run it from hbmenu with the console plugged into the PC over USB.
//
// Wire format (one line per active touch point, sent as ASCII):
//   T <finger_id> <x> <y> <diameter_x> <diameter_y> <rotation_angle> <delta_time> <sampling_number>\n
// A line "F <count>\n" is sent once per polled frame, even with 0 touches,
// so the PC side can tell "no touch" apart from "USB not receiving".
//
// delta_time and sampling_number come straight from the console's own hid
// timestamps, not from anything we measure externally - they tell you the
// real hardware sampling rate, uncontaminated by USB transfer or Python
// processing time.

#include <switch.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    consoleInit(NULL);

    Result rc = usbCommsInitialize();
    if (R_FAILED(rc)) {
        printf("usbCommsInitialize failed: 0x%x\n", rc);
        printf("Plug into a PC over USB and relaunch.\n");
        consoleUpdate(NULL);
    }

    // We only need button state to detect "+ to quit"; touch doesn't need
    // npad configuration at all, it's read straight from the hid service.
    padConfigureInput(1, HidNpadStyleSet_NpadStandard);
    PadState pad;
    padInitializeDefault(&pad);

    printf("touch_capture running. Touch the screen; press + to quit.\n");
    consoleUpdate(NULL);

    int frame = 0;
    while (appletMainLoop()) {
        padUpdate(&pad);
        u64 kDown = padGetButtonsDown(&pad);
        if (kDown & HidNpadButton_Plus) break;

        // Pull up to 8 buffered states per iteration instead of just the
        // newest one. The hid touch buffer is a small ring (16 entries),
        // so if our loop runs slower than the ~4ms hw sampling rate, we
        // can catch up on what we missed instead of silently dropping it.
        // states[0] is the newest; walk backwards to send oldest-first.
        HidTouchScreenState states[8] = {0};
        s32 total = hidGetTouchScreenStates(states, 8);

        for (s32 s = total - 1; s >= 0; s--) {
            HidTouchScreenState *st = &states[s];
            char line[32];
            int len = snprintf(line, sizeof(line), "F %d\n", (int)st->count);
            usbCommsWrite(line, len);

            for (s32 i = 0; i < st->count; i++) {
                HidTouchState *t = &st->touches[i];
                char tline[128];
                len = snprintf(tline, sizeof(tline), "T %u %u %u %u %u %u %llu %llu\n",
                    t->finger_id, t->x, t->y,
                    t->diameter_x, t->diameter_y, t->rotation_angle,
                    (unsigned long long)t->delta_time,
                    (unsigned long long)st->sampling_number);
                usbCommsWrite(tline, len);
            }
        }

        // consoleUpdate() blocks on vsync (~16ms) - that was the actual
        // bottleneck earlier, not touchscreen hardware or USB. Only touch
        // the screen occasionally so the polling loop above can run close
        // to the real ~4ms sampling rate instead of being throttled to it.
        if (++frame % 30 == 0) {
            printf("\x1b[1;0Hheld=0x%016llx        ",
                (unsigned long long)padGetButtons(&pad));
            consoleUpdate(NULL);
        }
    }

    usbCommsExit();
    consoleExit(NULL);
    return 0;
}
