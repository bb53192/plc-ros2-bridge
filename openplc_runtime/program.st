FUNCTION_BLOCK FB_Blinker
  VAR_INPUT
    enable : BOOL;
  END_VAR
  VAR_OUTPUT
    blink_out : BOOL;
  END_VAR
  VAR
    blink_timer : TON;
  END_VAR

  blink_timer(IN := enable AND NOT blink_timer.Q, PT := T#500ms);

  IF enable THEN
      IF blink_timer.Q THEN
          blink_out := NOT blink_out;
      END_IF;
  ELSE
      blink_out := FALSE;
      blink_timer(IN := FALSE, PT := T#500ms);
  END_IF;
END_FUNCTION_BLOCK


(* ==================================================================== *)
(* Cell cycle sequencer, gated by an enable input.                      *)
(*                                                                      *)
(* Drop this FB into the graphical main and wire:                       *)
(*     enable       := auto_mode        (TRUE only in AUTO)             *)
(*     part_present := part_present                                     *)
(*     robot_busy   := robot_busy                                       *)
(*     cycle_done   := cycle_done                                       *)
(*     belt_run     -> belt_run   (output)                              *)
(*     robot_start  -> robot_start(output)                              *)
(*                                                                      *)
(* When enable is FALSE (MANUAL / stopped) the outputs are forced low   *)
(* and the sequence is reset, so AUTO always restarts cleanly.          *)
(* ==================================================================== *)
FUNCTION_BLOCK FB_CellCycle
  VAR_INPUT
    enable       : BOOL;   (* AUTO mode: run the cycle *)
    part_present : BOOL;
    robot_busy   : BOOL;
    cycle_done   : BOOL;
  END_VAR
  VAR_OUTPUT
    belt_run    : BOOL;
    robot_start : BOOL;
    state       : INT;     (* 0 INIT, 1 RUN_BELT, 2 AT_END, 3 PICKING *)
  END_VAR

  IF enable THEN
    CASE state OF
      0: (* INIT *)
         belt_run    := FALSE;
         robot_start := FALSE;
         state := 1;

      1: (* RUN_BELT - vozi dok komad ne dodje do senzora *)
         belt_run := TRUE;
         IF part_present THEN
             state := 2;
         END_IF;

      2: (* AT_END - stani, okini robota *)
         belt_run    := FALSE;
         robot_start := TRUE;
         IF robot_busy THEN          (* start prihvacen *)
             state := 3;
         END_IF;

      3: (* PICKING - spusti start (edge!), cekaj kraj *)
         robot_start := FALSE;
         IF cycle_done THEN          (* komad je respawnan -> natrag na traku *)
             state := 1;
         END_IF;
    END_CASE;
  ELSE
    (* disabled (MANUAL / stopped): hold outputs low, reset sequence *)
    belt_run    := FALSE;
    robot_start := FALSE;
    state       := 0;
  END_IF;
END_FUNCTION_BLOCK


(* ==================================================================== *)
(* Manual command block, gated by an enable input.                      *)
(*                                                                      *)
(* In MANUAL the operator drives the cell straight from the HMI. The    *)
(* HMI writes man_belt_cmd / man_robot_cmd over OPC UA; this FB just     *)
(* passes them through while enabled, and forces them low otherwise so   *)
(* it never fights the AUTO block.                                       *)
(*                                                                      *)
(* Drop into the graphical main and wire:                               *)
(*     enable        := NOT auto_mode     (TRUE only in MANUAL)         *)
(*     man_belt_cmd  := man_belt_cmd      (HMI, OPC UA)                  *)
(*     man_robot_cmd := man_robot_cmd     (HMI, OPC UA)                  *)
(*     belt_run      -> (see combine below)                             *)
(*     robot_start   -> (see combine below)                             *)
(*                                                                      *)
(* Combine AUTO + MANUAL outputs in the graphical main. Because exactly  *)
(* one block is enabled at a time, an OR is enough:                      *)
(*     belt_run    := CELL_AUTO.belt_run    OR CELL_MAN.belt_run;        *)
(*     robot_start := CELL_AUTO.robot_start OR CELL_MAN.robot_start;     *)
(* (or, if you prefer explicit selection:                               *)
(*     belt_run := SEL(auto_mode, CELL_MAN.belt_run, CELL_AUTO.belt_run))*)
(* ==================================================================== *)
FUNCTION_BLOCK FB_CellManual
  VAR_INPUT
    enable        : BOOL;   (* MANUAL mode active *)
    man_belt_cmd  : BOOL;   (* HMI: run belt *)
    man_robot_cmd : BOOL;   (* HMI: trigger robot (rising edge starts a cycle) *)
  END_VAR
  VAR_OUTPUT
    belt_run    : BOOL;
    robot_start : BOOL;
  END_VAR

  IF enable THEN
    belt_run    := man_belt_cmd;
    robot_start := man_robot_cmd;
  ELSE
    belt_run    := FALSE;
    robot_start := FALSE;
  END_IF;
END_FUNCTION_BLOCK
