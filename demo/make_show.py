"""Write the made-up demo show, "Night Line", in the screenplay pack's format.

Everything here was invented for this repository. No character, place or line comes from the
confidential pack the tool was built on. Code lays out the times, so every line sits inside its
shot and every shot inside its scene, the way the real scripts are timed.

    python demo/make_show.py      # writes demo/Input/episode-00N-Episode 90000N.txt

The story: at 2 a.m. a late-night radio host takes a call from someone who knows what happened
on a flooded bridge ten years ago, and who says they are inside the building.
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent / "Input"
WORDS_PER_S = 2.4  # median speech rate in the real pack
GAP = 30           # centiseconds between blocks that play over the same shot


def shot(text: str, secs: float, *over: tuple) -> tuple:
    """A CAMERA block that lasts at least `secs`, with the lines, actions and sounds over it."""
    return ("CAMERA", text, round(secs * 100), over)


def say(label: str, line: str) -> tuple:
    return ("LINE", label, line)


def act(text: str, secs: float = 2.0) -> tuple:
    return ("ACTION", text, round(secs * 100))


def sfx(text: str, secs: float = 2.5) -> tuple:
    """A sound cue. It starts with the next line or action and does not hold it back."""
    return ("SOUND FX", text, round(secs * 100))


def tc(cs: int) -> str:
    return f"{cs // 6000:02d}:{cs % 6000 // 100:02d}.{cs % 100:02d}"


def layout(scenes: list) -> list:
    """Scenes of shots -> [(heading, start, end, [(label, start, end, body), ...]), ...] in centiseconds."""
    t, out = 0, []
    for heading, shots in scenes:
        scene_start, blocks = t, []
        for _, text, min_len, over in shots:
            cursor, end, inner = t + GAP, t + min_len, []
            for kind, a, b in over:
                if kind == "LINE":
                    dur = round((0.4 + len(b.split()) / WORDS_PER_S) * 10) * 10
                    inner.append((a, cursor, cursor + dur, f'"{b}"'))
                    cursor += dur + GAP
                elif kind == "ACTION":
                    inner.append(("ACTION", cursor, cursor + b, a))
                    cursor += b + GAP
                else:
                    inner.append(("SOUND FX", cursor, cursor + b, a))
                end = max(end, inner[-1][2] + 20)
            blocks.append(("CAMERA", t, end, text))
            blocks.extend(inner)
            t = end
        out.append((heading, scene_start, t, blocks))
    return out


STUDIO = "INT. HARBOR FM – STUDIO B – NIGHT"
STUDIO_CONT = "INT. HARBOR FM – STUDIO B – CONTINUOUS"
BOOTH = "INT. HARBOR FM – CONTROL BOOTH – CONTINUOUS"

EPISODES = [
    {
        "n": 1,
        "logline": "A late-night radio host takes a call from a stranger who knows what she did on a flooded "
                   "bridge ten years ago, and who says she is already inside the building.",
        "notes": "First episode of the show.",
        "scenes": [(STUDIO, [
            shot("Extreme close-up of a red ON AIR sign flickering to life in the dark.", 3.0,
                 sfx("A low synth drone hums under soft radio static.", 3.0)),
            shot("Medium shot of Nora at the microphone, headphones on, a mug of coffee going cold.", 4.5,
                 say("NORA (Warm, Unhurried)", "It's two in the morning, this is Night Line, and the lines are open.")),
            shot("Through the booth glass, Dev raises a hand and points at line three.", 3.0,
                 act("Dev taps a blinking button on the phone console.", 1.8)),
            shot("Close-up on Nora, smiling into the microphone.", 3.5,
                 say("NORA (Warm)", "Line three, you're on the air. What's keeping you up tonight?")),
            shot("Slow push-in on Nora as she listens.", 5.0,
                 say("THE CALLER (V.O.) (Calm, Cold)", "You are, Nora. You've been keeping me up for ten years.")),
            shot("Close-up on Nora's hand, frozen above the fader.", 3.0,
                 say("NORA (Thrown, Polite)", "I'm sorry, have we met?")),
            shot("Close-up on the phone console, line three glowing.", 4.5,
                 say("THE CALLER (V.O.) (Calm, Precise)", "The night of the flood. The old bridge on Mill Road. You were there.")),
            shot("Extreme close-up on Nora's eyes.", 3.0,
                 sfx("The drone drops to a low, ominous pulse.", 3.0)),
            shot("Through the glass, Dev leans into the talkback microphone.", 3.5,
                 say("DEV (O.S.) (Urgent, Whispering)", "Dump the call. Nora, dump it now.")),
            shot("Medium shot of Nora reaching for the cut switch.", 4.0,
                 say("THE CALLER (V.O.) (Cold, Threatening)", "Cut me off, and my next call is to the police.")),
            shot("Close-up on Nora's finger trembling above the switch.", 3.0,
                 act("Nora pulls her hand back from the switch.", 1.8)),
            shot("Two-shot through the glass: Nora and Dev staring at each other.", 3.5,
                 say("NORA (Shaken, Quiet)", "What do you want from me?")),
            shot("Close-up on the level meters jumping on the console.", 5.0,
                 say("THE CALLER (V.O.) (Calm, Cold)", "The truth. Live. Tell them who you left on that bridge.")),
            shot("Close-up on Nora, her breath catching.", 4.0,
                 say("NORA (Defiant, Shaking)", "I didn't leave anyone. I was seventeen. I jumped in after her.")),
            shot("Wide shot of the studio as every phone line on the console starts to blink.", 3.5,
                 sfx("A chorus of phone lines rings at once.", 3.0)),
            shot("Through the glass, Dev stares at his screen.", 3.5,
                 say("DEV (O.S.) (Stunned)", "Nora, the switchboard's full. Everyone is listening.")),
            shot("Close-up on Nora.", 3.5,
                 say("THE CALLER (V.O.) (Soft, Menacing)", "Good. Because I'm not calling from home.")),
            shot("Slow push-in on the studio door behind Nora; its small window is dark.", 4.0,
                 say("THE CALLER (V.O.) (Whispering)", "I'm in the building, Nora.")),
            shot("Wide shot of the corridor beyond the door as its lights go out one by one.", 4.0,
                 act("The corridor lights go out one by one.", 2.5),
                 sfx("A door buzzer sounds downstairs, and a hard cliffhanger sting cuts the hum dead.", 3.0)),
            shot("Extreme close-up on Nora's face, lit only by the red ON AIR sign. Cut to black.", 3.0),
        ])],
    },
    {
        "n": 2,
        "logline": "With the stranger downstairs and the whole city listening, the station manager orders Nora "
                   "to stay on air, and the caller turns her questions on Dev.",
        "notes": "Continues immediately after the Episode 1 cliffhanger: the caller says she is inside the building.",
        "scenes": [
            (STUDIO_CONT, [
                shot("Close-up on Nora in the red glow, frozen at the microphone.", 3.0,
                     sfx("The door buzzer sounds again, long and harsh.", 2.5)),
                shot("Through the glass, Dev slams the side of the flickering security monitor.", 3.5,
                     act("Dev slams the side of the security monitor.", 1.5),
                     say("DEV (O.S.) (Panicked)", "Lobby camera's back. Someone's at the front desk.")),
                shot("Insert: the grainy monitor shows a woman in a yellow raincoat, hood up, staring into the lens.", 4.0),
                shot("Close-up on Nora.", 4.0,
                     say("NORA (Steadying, On Air)", "Whoever you are, the doors are locked. Talk to me from there.")),
                shot("Close-up on line three, still glowing.", 4.0,
                     say("THE CALLER (V.O.) (Amused, Cold)", "You always did talk to people through glass.")),
            ]),
            (BOOTH, [
                shot("The booth door bangs open; the station manager strides in, a coat over his pyjamas.", 4.0,
                     act("The manager barges into the booth, phone in hand.", 1.8),
                     say("STATION MANAGER (Hollis) (Breathless, Thrilled)", "Don't you dare dump this call. We're trending.")),
                shot("Two-shot of Dev and Hollis.", 5.0,
                     say("DEV (Furious)", "There's a stranger in the lobby and you're checking ratings?"),
                     say("STATION MANAGER (Hollis) (Cold)", "There's a stranger in the lobby and forty thousand people listening. Keep her talking.")),
            ]),
            (STUDIO_CONT, [
                shot("Medium shot of Nora pulling off her headphones and turning to the glass.", 3.5,
                     say("NORA (Sharp)", "Dev. How does she have my private number?")),
                shot("Close-up on Dev through the glass, avoiding her eyes.", 3.0,
                     say("DEV (O.S.) (Evasive)", "I don't know. It came through the main line.")),
                shot("Close-up on Nora, headphones back on.", 4.0,
                     say("THE CALLER (V.O.) (Calm, Precise)", "He's lying, Nora. Ask him why he stopped driving after that night.")),
                shot("Extreme close-up on Dev's hand gripping the edge of the console.", 2.5,
                     sfx("A low string sting.", 2.0)),
                shot("Medium shot of Nora rising from her chair.", 4.0,
                     say("NORA (Confused, Rising Anger)", "Dev wasn't there. He was at home. He told the police.")),
                shot("Close-up on the phone console.", 4.5,
                     say("THE CALLER (V.O.) (Cold, Deliberate)", "Then ask him whose car they pulled out of the river the next morning.")),
                shot("Slow push-in on Dev through the glass as the colour drains from his face.", 3.5),
                shot("Close-up on Nora.", 2.5,
                     say("NORA (Quiet, Dangerous)", "Dev. Look at me.")),
                shot("Wide shot of the booth: Dev reaching for the door, Hollis blocking it.", 4.0,
                     act("Dev lunges for the door; Hollis plants a hand on his chest.", 2.0),
                     say("HOLLIS (Smug)", "Nobody leaves while we're live.")),
                shot("Close-up on the security monitor: the lobby is empty, the front door swinging shut.", 3.0,
                     sfx("The door buzzer cuts off mid-tone.", 1.5)),
                shot("Close-up on Nora, staring at the monitor through the glass.", 3.5,
                     say("THE CALLER (V.O.) (Whispering, Close)", "Second floor, Nora. Third door on the left.")),
                shot("Slow push-in on the studio door as its handle begins to turn.", 4.0,
                     act("The studio door handle turns slowly.", 2.5),
                     sfx("The synth climbs into a sharp cliffhanger sting.", 2.5)),
                shot("Cut to black.", 1.5),
            ]),
        ],
    },
    {
        "n": 3,
        "logline": "The woman in the yellow raincoat walks in, the girl everyone believed drowned, and Dev's "
                   "secret comes out live on air.",
        "notes": "Continues immediately after the Episode 2 cliffhanger: the studio door handle starts to turn.",
        "scenes": [
            (STUDIO_CONT, [
                shot("Close-up on the turning handle.", 2.5,
                     act("The door swings open.", 1.2)),
                shot("Wide shot: a woman in a yellow raincoat stands dripping in the doorway, her phone glowing in her hand.", 3.5,
                     sfx("Rain roars in the stairwell behind her.", 3.0)),
                shot("Close-up on Nora, backing against the desk.", 2.5,
                     say("NORA (Terrified)", "Who are you?")),
                shot("Close-up on the woman as she lowers her hood.", 4.5,
                     act("The woman lowers her hood; a pale scar runs along her jaw.", 2.0),
                     say("WOMAN IN YELLOW RAINCOAT (Maya) (Steady, Hurt)", "You knew me as Maya. You held on to me in the river.")),
                shot("Extreme close-up on Nora's face.", 3.5,
                     say("NORA (Stunned, Whispering)", "Maya Cole? They never found you. They said you drowned.")),
                shot("Two-shot of Nora and Maya.", 5.0,
                     say("MAYA (Bitter)", "I washed up two miles downstream. Someone drove onto that bridge with no lights, and drove away when I went over.")),
                shot("Through the glass, Dev backs away from the window.", 2.5,
                     sfx("A low heartbeat pulse.", 2.5)),
                shot("Medium shot of Nora turning to the glass.", 4.0,
                     say("NORA (Breaking)", "You told me it was my fault. You said I panicked and let go of her.")),
            ]),
            (BOOTH, [
                shot("Nora storms into the booth.", 2.5,
                     act("Nora shoves the booth door open.", 1.2)),
                shot("Close-up on Dev.", 4.0,
                     say("DEV (Desperate, Pleading)", "I was nineteen. I'd been drinking. I thought she was gone.")),
                shot("Close-up on Nora.", 2.0,
                     act("Nora slaps Dev across the face.", 0.8)),
                shot("Close-up on Dev, his cheek reddening.", 2.5,
                     sfx("The slap rings out over the live microphone.", 1.5)),
                shot("Medium shot of Nora, tears on her face.", 4.5,
                     say("NORA (Furious, Tearful)", "Ten years. Ten years I carried her, and you let me.")),
                shot("Hollis, giddy, holds up his phone to show a live listener graph.", 3.5,
                     say("HOLLIS (Delighted)", "Keep going. Sixty thousand and climbing.")),
                shot("Close-up on Maya in the booth doorway.", 5.0,
                     say("MAYA (Cold)", "Turn it off, Mr. Hollis. Or tell them how much you paid to keep your nephew's car out of the papers.")),
                shot("Extreme close-up on Hollis, his smile frozen.", 2.5,
                     sfx("A sharp string sting.", 1.5)),
                shot("Close-up on Nora.", 3.5,
                     say("NORA (Slow, Realising)", "Your nephew. You hired him knowing what he did.")),
                shot("Wide shot of the booth as blue and red lights sweep across the window.", 3.5,
                     sfx("Police sirens rise outside.", 3.0)),
                shot("Close-up on Hollis, slipping his phone into his coat.", 4.5,
                     say("HOLLIS (Cold, Threatening)", "Say one more word on air, and none of you will ever work in radio again.")),
                shot("Extreme close-up on Nora's hand moving to the master fader.", 3.0,
                     sfx("The synth hits a cliffhanger high and holds it.", 2.5)),
                shot("Cut to black.", 1.5),
            ]),
        ],
    },
    {
        "n": 4,
        "logline": "Nora tells the city the truth before the police reach the booth, then learns the woman in "
                   "the raincoat never made the call.",
        "notes": "Continues immediately after the Episode 3 cliffhanger: Hollis threatens Nora as the police arrive.",
        "scenes": [
            (BOOTH, [
                shot("Close-up on Nora's hand on the master fader.", 3.0,
                     say("NORA (Steady, Resolute)", "Night Line listeners, stay with me.")),
                shot("Medium shot of Nora leaning into the booth microphone.", 5.5,
                     say("NORA (Clear, Resolute)", "The man who owns this station paid to bury a crash that nearly killed a girl. She is standing next to me.")),
                shot("Close-up on Hollis lunging for the console.", 2.5,
                     act("Hollis grabs for the fader; Dev shoves him back against the wall.", 2.0)),
                shot("Close-up on Dev holding Hollis in place.", 3.5,
                     say("DEV (Shaking, Resolute)", "It's over. I'll tell them myself.")),
            ]),
            ("INT. HARBOR FM – STAIRWELL – CONTINUOUS", [
                shot("Police flashlights sweep up the stairwell.", 3.0,
                     say("OFFICER REYES (O.S.) (Commanding)", "Harbor Police! Nobody move!")),
            ]),
            (BOOTH, [
                shot("Wide shot as Officer Reyes enters the booth, a hand on her radio.", 3.5,
                     say("OFFICER REYES (Firm)", "Which one of you is Dev Marsh?")),
                shot("Close-up on Dev raising his hands.", 4.0,
                     say("DEV (Quiet, Broken)", "Me. I was driving the car on Mill Road.")),
                shot("Medium shot: Reyes cuffs Dev while Hollis edges toward the door.", 4.0,
                     act("Reyes cuffs Dev's wrists.", 1.8),
                     say("OFFICER REYES (Firm)", "Mr. Hollis, you're coming too.")),
                shot("Close-up on Nora, exhausted, as she pulls the fader down.", 3.0,
                     act("Nora pulls the master fader down.", 1.2),
                     sfx("The live hum cuts to silence.", 1.5)),
                shot("The ON AIR sign goes dark.", 2.5),
            ]),
            (STUDIO, [
                shot("Two-shot of Nora and Maya in the empty studio, rain streaking the window.", 5.0,
                     say("NORA (Soft, Tearful)", "I'm sorry. I let go of you."),
                     say("MAYA (Soft)", "You jumped in after me. Nobody else did.")),
                shot("Close-up on Maya as she holds out her phone.", 4.5,
                     say("MAYA (Uneasy)", "But Nora, you need to know something. I didn't call you tonight.")),
                shot("Extreme close-up on Nora.", 2.0,
                     say("NORA (Confused)", "What?")),
                shot("Insert: Maya's phone shows a message from an unknown number: Be at Harbor FM at 2 a.m.", 4.0),
                shot("Close-up on the studio console as line three lights up again.", 3.0,
                     sfx("A single phone line rings in the silent studio.", 2.5)),
                shot("Slow push-in on Nora lifting the receiver.", 4.0,
                     act("Nora lifts the receiver.", 1.2),
                     say("THE CALLER (V.O.) (Calm, Cold)", "Nice show, Nora. Now let's talk about what you did.")),
                shot("Extreme close-up on Nora's face as the ON AIR sign flickers back on by itself.", 3.5,
                     sfx("The synth snaps back on with a cliffhanger sting.", 2.5)),
                shot("Cut to black.", 1.5),
            ]),
        ],
    },
]


def render(ep: dict) -> str:
    lines = [f"EPISODE NUMBER: Episode {ep['n']}", f"EPISODE LOGLINE: {ep['logline']}",
             f"CONTINUITY NOTES: {ep['notes']}", "", "--- SCRIPT START ---", ""]
    for i, (heading, start, end, blocks) in enumerate(layout(ep["scenes"]), 1):
        lines += [f"SCENE {i:02d}", f"Scene heading: {heading}", f"Scene start: {tc(start)}",
                  f"Scene end: {tc(end)}", "", "Actions and dialogue:", ""]
        for label, s, e, body in blocks:
            lines += [f"**{label}:**", f"Start: ({tc(s)}) | End: ({tc(e)})", body, ""]
    return "\n".join(lines + ["--- SCRIPT END ---", ""])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for ep in EPISODES:
        path = OUT / f"episode-{ep['n']:03d}-Episode {900000 + ep['n']}.txt"
        path.write_text(render(ep), encoding="utf-8")
        print(f"wrote {path.relative_to(OUT.parent.parent)}")


if __name__ == "__main__":
    main()
