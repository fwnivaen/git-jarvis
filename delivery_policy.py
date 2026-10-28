"""Pure quiet-hours, speech and resource decisions; no presence inference."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

TIMEZONE = 'America/New_York'


def local_time(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ':':
        raise ValueError('Use a local time in HH:MM form.')
    try:
        result = time.fromisoformat(value)
    except ValueError:
        raise ValueError('Use a local time in HH:MM form.') from None
    if result.isoformat(timespec='minutes') != value:
        raise ValueError('Use a local time in HH:MM form.')
    return result


def aware_now(value=None):
    result = datetime.now(timezone.utc) if value is None else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Supply an aware time.')
    return result


def quiet_now(*, enabled=False, start='22:00', end='08:00', now=None):
    if type(enabled) is not bool:
        raise ValueError('Quiet hours need a boolean setting.')
    beginning, ending = local_time(start), local_time(end)
    if beginning == ending:
        raise ValueError('Quiet hours need different start and end times.')
    clock = aware_now(now).astimezone(ZoneInfo(TIMEZONE)).time().replace(tzinfo=None)
    return enabled and (beginning <= clock < ending if beginning < ending else
        clock >= beginning or clock < ending)


def decide(*, now=None, quiet_enabled=False, quiet_start='22:00', quiet_end='08:00',
           active_conversation=False, muted=False, speak_updates=False, attention=False):
    for value in (active_conversation, muted, speak_updates, attention):
        if type(value) is not bool:
            raise ValueError('Delivery switches must be booleans.')
    quiet = quiet_now(enabled=quiet_enabled, start=quiet_start, end=quiet_end, now=now)
    if active_conversation:
        mode, reason = 'defer', 'active_conversation'
    elif quiet and not attention:
        mode, reason = 'defer', 'quiet_hours'
    elif quiet:
        mode, reason = 'visual_only', 'attention_during_quiet_hours'
    elif muted:
        mode, reason = 'visual_only', 'muted'
    elif not speak_updates:
        mode, reason = 'visual_only', 'speech_not_enabled'
    else:
        mode, reason = 'visual_and_speech', 'eligible'
    return {'mode': mode, 'reason': reason, 'quiet': quiet, 'timezone': TIMEZONE,
        'show_visual': mode != 'defer', 'speak': mode == 'visual_and_speech',
        'defer': mode == 'defer', 'presence_inferred': False}


def resource_gate(signals, *, minimum_battery_percent=20, external_power_required=False,
                  network_required=False):
    if (type(minimum_battery_percent) is not int or not 0 <= minimum_battery_percent <= 100
            or type(external_power_required) is not bool or type(network_required) is not bool):
        raise ValueError('Invalid resource settings.')
    source = signals if isinstance(signals, dict) else {}
    battery = source.get('battery') if isinstance(source.get('battery'), dict) else {}
    network = source.get('network') if isinstance(source.get('network'), dict) else {}
    battery_known = battery.get('status') == 'known'
    network_known = network.get('status') == 'known'
    if external_power_required and (not battery_known or battery.get('power_source') != 'external'):
        return {'allow': False, 'reason': 'external_power_required' if battery_known else 'power_unknown'}
    percent = battery.get('percent')
    if (battery_known and battery.get('power_source') == 'battery' and type(percent) is int
            and 0 <= percent < minimum_battery_percent):
        return {'allow': False, 'reason': 'low_battery'}
    if network_required and (not network_known or network.get('configured') is not True):
        return {'allow': False, 'reason': 'network_not_configured' if network_known else 'network_unknown'}
    return {'allow': True, 'reason': 'eligible', 'internet_verified': False}
