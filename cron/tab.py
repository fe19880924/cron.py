import calendar
import datetime


FIELDS = MINUTE, HOUR, DOM, MONTH, DOW = range(5)

MONTH_NAMES = 'jan feb mar apr may jun jul aug sep oct nov dec'.split()
DOW_NAMES = 'sun mon tue wed thu fri sat sun'.split()

MINUTE_INFO = 0, 59, None
HOUR_INFO = 0, 23, None
DOM_INFO = 1, 31, None
MONTH_INFO = 1, 12, MONTH_NAMES
DOW_INFO = 0, 7, DOW_NAMES

FIELD_INFO = MINUTE_INFO, HOUR_INFO, DOM_INFO, MONTH_INFO, DOW_INFO

PREDEFINED = {
    '@yearly':  '0 0 1 1 *',
    '@monthly': '0 0 1 * *',
    '@weekly':  '0 0 * * 0',
    '@daily':   '0 0 * * *',
    '@hourly':  '0 * * * *',
    }

PREDEFINED['@annually'] = PREDEFINED['@yearly']
PREDEFINED['@midnight'] = PREDEFINED['@daily']


class CronTabError(Exception):
    pass


class CronTab(object):
    pass


class CronTabEntry(object):

    def __init__(self, entry, fields, command, **kwargs):
        self.entry = entry
        self.fields = fields
        self.command = command
        self.dom_or_dow_star = kwargs.get('dom_or_dow_star', False)
        self.when_reboot = kwargs.get('when_reboot', False)

    def iter_field(self, field):
        '''Iterate through the matching values for a field.'''
        lo = FIELD_INFO[field][0]
        fields = self.fields[field]
        start = 0
        for i in range(fields.count(True)):
            last_index = fields.index(True, start)
            yield last_index + lo
            start = last_index + 1

    def next(self):
        return next(iter(self))

    def __iter__(self):
        '''Find future datetimes that this entry should be run.'''
        now = datetime.datetime.now()
        year = now.year
        DOM_LO, DOM_HI, _ = FIELD_INFO[DOM]
        while True:
            same_year = year == now.year
            for month in self.iter_field(MONTH):
                if same_year and month < now.month:
                    continue
                same_month = same_year and month == now.month
                num_days = calendar.monthrange(year, month)[1]
                # Iterate through all doms, and check later.
                # See Paul Vixie's comment below.
                for dom in range(DOM_LO, DOM_HI + 1):
                    if dom > num_days or same_month and dom < now.day:
                        continue
                    same_day = same_year and dom == now.day
                    for hour in self.iter_field(HOUR):
                        if same_day and hour < now.hour:
                            continue
                        same_hour = same_day and hour == now.hour
                        for minute in self.iter_field(MINUTE):
                            if same_hour and minute < now.minute + 1:
                                continue
                            dt = datetime.datetime(
                                    year, month, dom, hour, minute)
                            dow = dt.isoweekday()
    # From Paul Vixie's cron:
    #/* the dom/dow situation is odd.  '* * 1,15 * Sun' will run on the
    # * first and fifteenth AND every Sunday;  '* * * * Sun' will run *only*
    # * on Sundays;  '* * 1,15 * *' will run *only* the 1st and 15th.  this
    # * is why we keep 'e->dow_star' and 'e->dom_star'.  yes, it's bizarre.
    # * like many bizarre things, it's the standard.
    # */
                            valid_dom = dom in list(self.iter_field(DOM))
                            valid_dow = dow in list(self.iter_field(DOW))
                            if self.dom_or_dow_star:
                                valid = valid_dom and valid_dow
                            else:
                                valid = valid_dom or valid_dow

                            if valid:
                                yield dt

            # Try next year.
            year += 1              


def parse_entry(entry):
    '''Parse a single crontab entry.'''

    entry = entry.strip()

    # Reboot is a special case.
    if entry.lower().startswith('@reboot'):
        command = entry[7:].lstrip()
        return CronTabEntry(entry, None, command, when_reboot=True)

    # Replace predefined time specifiers.
    if entry.startswith('@'):
        try:
            token = entry.split()[0]
            val = PREDEFINED[token.lower()]
            entry = entry.replace(token, PREDEFINED[token], 1)
        except KeyError:
            mesg = 'bad time specifier: {!r}'.format(val)
            raise CronTabError(mesg)

    fields = []
    command = entry[::]
    kwargs = {}

    # Parse the fields.
    for expr, field in zip(entry.split(), FIELDS):
        command = command[len(expr):].lstrip()
        try:
            bits = parse_field(expr.lower(), field, kwargs)
            fields.append(bits)
        except ValueError:
            mesg = 'error parsing field: {!r}'.format(expr)
            raise CronTabError(mesg)

    # The command is whatever is left.
    command = command.strip()

    if len(fields) < len(FIELDS) or not command:
        mesg = 'error parsing entry {!r}'.format(entry)
        raise CronTabError(mesg)

    return CronTabEntry(entry, fields, command, **kwargs)


def parse_field(expr, field, kwargs={}):
    '''Parse a field from a crontab entry.'''

    if not expr:
        raise ValueError('empty expression')

    lo, hi, names = FIELD_INFO[field]
    bits = [False for i in range(hi - lo + 1)]

    # Replace names.
    if names is not None:
        for i, name in enumerate(names, lo):
            if name in expr:
                expr = expr.replace(name, str(i))

    # Iterate through comma separated values.
    for val in expr.split(','):
        step = 1
        if '/' in val:
            # Slash changes the step amount.
            val, step = val.split('/')
            try:
                step = int(step)
            except ValueError as e:
                raise ValueError(expr)
            if step < 1:
                raise ValueError('step value must be greater than zero')

        if not val:
            raise ValueError(expr)

        if val == '*':
            # Set the DOM/DOW flag.
            if field in (DOM, DOW):
                kwargs['dom_or_dow_star'] = True
            # Asterisk means to include all values.
            start, stop = lo, hi
        elif '-' in val:
            # Dash indicates a range of values.
            start, stop = val.split('-')
            if not start or not stop:
                raise ValueError(expr)
            start, stop = int(start), int(stop)
        else:
            # Only a single number, not a range.
            val = int(val)
            if not (lo <= val <= hi):
                raise ValueError('out of range: {}'.format(val))
            bits[int(val) - lo] = True
            continue

        # Make sure that start is less than stop. Also, make an exception
        # for Sunday-Sunday, because that makes sense intuitively.
        if field == DOW and start % 7 == stop % 7:
            start, stop = 0, 7
        if start >= stop:
            raise ValueError('start must be less than stop: {!r}'.format(val))

        # Set all values in the range.
        for i in range(start, stop + 1, step):
            if not (lo <= i <= hi):
                raise ValueError('out of range: {}'.format(i))
            bits[i - lo] = True

    # Both 0 and 7 are Sunday.
    if field == DOW and (bits[0] or bits[7]):
        bits[0] = bits[7] = True
     
    return bits
