# Home Assistant automation example

`govee_smart_music_dj_upstairs_v3.yaml` is the user's upstairs DJ script with new analyzer fields and `light_reaction_aggression` settings 1–4. It is **not installed by the add-on**; import into Home Assistant manually if desired.

The script expects the original device/entity naming, TV Govee select entity, WLED entities, history helper, `input_datetime.wled_gledopto_last_change`, and new **date-and-time** `input_datetime.wled_gledopto_last_reaction` helper. Confirm actual entities under Developer Tools > States. Run with low aggression initially, then adjust thresholds against real audio; very short attack flags can be missed unless the script is called frequently.

Palette selection stays under the main DJ automation's control. The script does not change WLED color palette.
