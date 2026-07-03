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

PROGRAM main
  VAR
    in1 : BOOL := 1;
    out1 : BOOL;
    FB_BLINKER1 : FB_Blinker;
    Alarm : BOOL;
  END_VAR
  VAR
    Motion_Sensor_0 AT %IX0.0 : BOOL;
    Door_Closed_0 AT %IX0.1 : BOOL;
  END_VAR

  FB_BLINKER1(enable := in1);
  out1 := FB_BLINKER1.blink_out;
  Alarm := NOT(Motion_Sensor_0) AND Door_Closed_0;
END_PROGRAM


CONFIGURATION Config0

  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms,PRIORITY := 1);
    PROGRAM instance0 WITH task0 : main;
  END_RESOURCE
END_CONFIGURATION
