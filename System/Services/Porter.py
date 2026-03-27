import random
from typing import Any, List, Dict, Optional

from loguru import logger

from System.Services import GlyphEffects
from System.Common.Constants import *

# yes, this function is ai generated AND DONT EVEN ASK ABOUT THIS FUNCTION, IT MAKES ME RAGE, IT TRIGGERS ME, DONT EVER FUCKING REMIND ME ABOUT THIS SH*T F**K MOTHERF**K
def port_segments_func(old_segments, new_segments, active, dup_threshold_ratio=0.5):
    if not active:
        return []

    if old_segments <= 1:
        k_pos = 0.0
    
    else:
        k_pos = (new_segments - 1) / (old_segments - 1)

    rounded = [round(a * k_pos) for a in active]
    rounded = [max(0, min(new_segments - 1, r)) for r in rounded]

    dup_count = sum(1 for i in range(1, len(rounded)) if rounded[i] == rounded[i-1])

    if dup_count >= dup_threshold_ratio * len(active):
        uniq = []
        
        for r in rounded:
            if not uniq or r != uniq[-1]:
                uniq.append(r)
        
        mapped = [uniq[0]]
        
        for i in range(1, len(uniq)):
            gap = max(1, uniq[i] - uniq[i-1])
            mapped.append(mapped[-1] + gap)
    
    else:
        mapped = [rounded[0]]
        
        for i in range(1, len(active)):
            gap_old = active[i] - active[i-1]
            gap_new = max(1, round(gap_old * k_pos))
            mapped.append(mapped[-1] + gap_new)

    if mapped[-1] > new_segments - 1:
        overflow = mapped[-1] - (new_segments - 1)
        mapped = [max(0, m - overflow) for m in mapped]

    final = []
    
    for m in mapped:
        mm = max(0, min(new_segments - 1, m))
        
        if not final or mm != final[-1]:
            final.append(mm)

    return final

class Port:
    @staticmethod
    def process_dict_track(glyph: Dict[str, Any], track: Dict[str, Any]) -> Any:
        mode = track.get("mode")
        logger.debug(f"Processing dict track. Mode: '{mode}'. Full track data: {track}")
        
        if mode == "random":
            variants = track.get("variants", [])
            
            if variants:
                chosen_variant = random.choice(variants)
                logger.info(f"Random mode triggered. Selected variant '{chosen_variant}' from {len(variants)} available options.")
                return chosen_variant
            
            else:
                logger.warning("Random mode triggered, but 'variants' list is empty!")
        
        return track

    @staticmethod
    def get_target_track(port_from: str, port_to: str, glyph: Dict[str, Any]) -> Any:
        from_track = glyph["track"]
        logger.debug(f"Resolving target track for '{from_track}' from model '{port_from}' to '{port_to}'")
        
        model_map = PortMaps[port_from]["to"][port_to]
        track = model_map[from_track]

        if "effect" in glyph:
            effect_name = glyph["effect"]["name"]
            
            if effect_name in GlyphEffects.only_segmented():
                logger.info(f"Detected segmented effect: '{effect_name}'. Routing to segment map.")
                return model_map["effects"]["segments"][from_track]

        if "segments" in glyph:
            logger.debug(f"Glyph contains segments. Routing to segment map for track '{from_track}'.")
            return model_map["effects"]["segments"][from_track]

        if isinstance(track, list):
            if track and isinstance(track[0], dict):
                logger.debug(f"Resolved track is a list of dictionaries. Passing to process_dict_track for randomization.")
                return Port.process_dict_track(glyph, random.choice(track))
            
            logger.debug(f"Resolved track is a simple list: {track}")
            return track
        
        if isinstance(track, dict):
            logger.debug(f"Resolved track is a dictionary. Passing to process_dict_track.")
            return Port.process_dict_track(glyph, track)

        logger.debug(f"Resolved target track to direct value: {track}")
        return track
    
    @staticmethod
    def _make_glyph(base_glyph: Dict[str, Any], track: Any, segments: Optional[List] = None, copy_effects: bool = False) -> Dict[str, Any]:
        logger.debug(f"Constructing new glyph. Target track: '{track}', Segments overriding: {segments is not None}")
        new_glyph = base_glyph.copy()
        new_glyph["track"] = track

        if segments is None:
            new_glyph.pop("segments", None)
        
        else:
            new_glyph["segments"] = list(segments)

        if copy_effects and "effects" in new_glyph:
            logger.debug("Copying effects payload into the new glyph.")
            new_glyph["effects"] = [effect.copy() for effect in new_glyph["effects"]]

        return new_glyph

    @staticmethod
    def port_glyphs(port_to: str, composition) -> List[Dict[str, Any]]:
        singles, effects = composition.sorted_glyphs()
        ported_glyphs = []

        logger.info(f"Starting porting process to '{port_to}'. Models found: {len(singles)} singles, {len(effects)} effects.")

        for effect in effects:
            logger.debug(f"--- Processing Effect --- Initial Track: {effect.get('track')}")
            target_track = Port.get_target_track(composition.model, port_to, effect)
            logger.info(f"Target track mapped to: {target_track}")

            if "segments" in effect:
                seg_src = DEVICES[composition.model].segments_map[effect["track"]]
                seg_dst = DEVICES[port_to].segments_map[target_track[0]]
                logger.debug(f"Porting segments. Source map: {seg_src}, Dest map: {seg_dst}, Active: {effect['segments']}")
                
                ported_segments = port_segments_func(seg_src, seg_dst, effect["segments"])

                eff_for_conversion = effect.copy()
                eff_for_conversion["track"] = target_track[0]
                eff_for_conversion["segments"] = ported_segments

                list_of_glyphs = GlyphEffects.effect_to_glyph(eff_for_conversion, composition.bpm, port_to)
                ported_glyphs.extend(list_of_glyphs)
                logger.debug(f"Appended {len(list_of_glyphs)} converted segment glyphs to the output list.")

            for track in target_track:
                if isinstance(track, tuple):
                    tr, segment = track
                    eff_copy = Port._make_glyph(effect, tr, segments=[segment], copy_effects=True)
                
                else:
                    eff_copy = Port._make_glyph(effect, track, segments=None, copy_effects=True)

                logger.debug(f"Generating effect-based glyph. Track: {eff_copy['track']}, Segments: {eff_copy.get('segments')}")
                list_of_glyphs = GlyphEffects.effect_to_glyph(eff_copy, composition.bpm, port_to)
                ported_glyphs.extend(list_of_glyphs)
                logger.debug(f"Appended {len(list_of_glyphs)} standard effect glyphs to the output list.")

        for single in singles:
            logger.debug(f"--- Processing Single Glyph --- Initial Track: {single.get('track')}")
            target_track = Port.get_target_track(composition.model, port_to, single)
            logger.info(f"Target track mapped to: {target_track}")

            if "segments" not in single:
                for track in target_track:
                    if isinstance(track, tuple):
                        tr, segment = track
                        new_glyph = Port._make_glyph(single, tr, segments=[segment])
                    else:
                        new_glyph = Port._make_glyph(single, track, segments=None)

                    logger.debug(f"Adding simple unsegmented glyph -> Track: {new_glyph['track']}, Segments: {new_glyph.get('segments')}")
                    ported_glyphs.append(new_glyph)

            else:
                chosen_target = target_track[0]
                port_segments_from = DEVICES[composition.model].segments_map[single["track"]]
                port_segments_to = DEVICES[port_to].segments_map[chosen_target]
                
                logger.debug(f"Porting segments for complex single. Source map: {port_segments_from}, Dest map: {port_segments_to}")
                ported_segments = port_segments_func(port_segments_from, port_segments_to, single["segments"])

                new_single = Port._make_glyph(single, chosen_target, segments=ported_segments)
                logger.debug(f"Adding complex segmented single -> Track: {new_single['track']}, Segments: {new_single.get('segments')}")
                ported_glyphs.append(new_single)
        
        logger.info(f"Porting completed successfully. Total ported glyphs generated: {len(ported_glyphs)}")
        
        return ported_glyphs