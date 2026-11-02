"""Read-only host/process measurements and bounded training admission.

`availableEstimateBytes` is explicitly a reclaimable-page estimate on macOS,
not a promise that the OS will allocate that memory. MLX peak bytes are measured
by the training worker separately. No process command lines are collected.
"""
import math
import platform
import re
import subprocess
import time
from pathlib import Path

MIB = 1024**2
LIMITS = {'maxProcessGroupRssBytes': 3*1024**3,
          'minimumAvailableEstimateBytes': 512*MIB,
          'maxSwapGrowthBytes': 768*MIB,
          'consecutivePressureSamples': 2}


def _command(args):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=3).stdout


def parse_macos(vm_stat, swap, physical):
    page = re.search(r'page size of (\d+) bytes', vm_stat)
    if not page:
        raise ValueError('Unknown macOS page size')
    fields = dict(re.findall(r'^([\w ]+):\s+(\d+)\.', vm_stat, re.M))
    available = sum(int(fields[key]) for key in ('Pages free','Pages inactive','Pages speculative'))*int(page[1])
    used = re.search(r'used\s*=\s*([\d.,]+)([KMGT]?)',swap)
    if not used:
        raise ValueError('Unknown macOS swap usage')
    amount = float(used[1].replace(',','.'))
    swap_used = int(amount*1024**(' KMGT'.index(used[2]) if used[2] else 0))
    physical = int(physical.strip())
    if not 0 <= available <= physical or physical < MIB or swap_used < 0:
        raise ValueError('Invalid host memory metrics')
    return {'physicalBytes':physical,'availableEstimateBytes':available,'swapUsedBytes':swap_used,
            'availableMethod':'free+inactive+speculative-pages'}


def process_group_rss(text, pid):
    if type(pid) is not int or pid <= 0:
        return None
    rows = []
    for line in text.splitlines():
        values = line.split()
        if len(values)==3 and all(value.isdigit() for value in values):
            rows.append(tuple(map(int,values)))
    own = next((row for row in rows if row[0]==pid),None)
    if own is None:
        return None
    # Only an explicitly created process-group leader grants group ownership.
    return sum(row[2] for row in rows if row[1]==pid)*1024 if own[1]==pid else own[2]*1024


def sample(pid=None):
    value = {'at':time.time(),'platform':platform.system(),'supported':False}
    try:
        if platform.system()=='Darwin':
            value.update(parse_macos(_command(['/usr/bin/vm_stat']),
                _command(['/usr/sbin/sysctl','-n','vm.swapusage']),
                _command(['/usr/sbin/sysctl','-n','hw.memsize'])))
            value['supported']=True
        elif platform.system()=='Linux':
            fields = {key:int(number)*1024 for key,number in re.findall(r'^(\w+):\s+(\d+) kB',Path('/proc/meminfo').read_text(),re.M)}
            value.update(physicalBytes=fields['MemTotal'],availableEstimateBytes=fields['MemAvailable'],
                         swapUsedBytes=fields['SwapTotal']-fields['SwapFree'],availableMethod='MemAvailable',supported=True)
        if pid is not None:
            value['processGroupRssBytes']=process_group_rss(_command(['/bin/ps','-axo','pid=,pgid=,rss=']),pid)
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        value['measurementError']=True
    return value


class ResourceGuard:
    def __init__(self, sampler=sample, limits=None):
        self.sampler=sampler
        self.limits={**LIMITS,**(limits or {})}
        if set(self.limits)!=set(LIMITS) or any(type(value) is not int or value<1 for value in self.limits.values()):
            raise ValueError('Invalid resource limits')
        self.samples=[]
        self.pressure_count=0
        self.baseline_swap=None
        self.reason=None

    def check(self,pid=None,*,admission=False):
        current=self.sampler(pid)
        if not isinstance(current,dict):
            current={'supported':False,'measurementError':True}
        self.samples.append(current)
        self.samples=self.samples[-720:]
        if not current.get('supported') or current.get('measurementError'):
            # On the intended Mac, a failed measurement is not permission for a
            # background job to allocate several GB behind an interactive task.
            self.reason='measurement-unavailable' if current.get('platform')=='Darwin' else None
            return self.reason
        def number(key):
            value=current.get(key)
            return value if type(value) in (int,float) and math.isfinite(value) and value>=0 else None
        swap=number('swapUsedBytes')
        if self.baseline_swap is None:self.baseline_swap=swap
        rss=number('processGroupRssBytes')
        available=number('availableEstimateBytes')
        reason = ('process-rss-limit' if rss is not None and rss>self.limits['maxProcessGroupRssBytes'] else
                  'available-memory-low' if available is not None and available<self.limits['minimumAvailableEstimateBytes'] else
                  'swap-growth-limit' if swap is not None and self.baseline_swap is not None and swap-self.baseline_swap>self.limits['maxSwapGrowthBytes'] else None)
        self.pressure_count=self.pressure_count+1 if reason else 0
        self.reason=reason if reason and (admission or self.pressure_count>=self.limits['consecutivePressureSamples']) else None
        return self.reason

    def receipt(self):
        rss=[row.get('processGroupRssBytes') for row in self.samples]
        available=[row.get('availableEstimateBytes') for row in self.samples]
        numeric=lambda values:[value for value in values if type(value) in (int,float) and math.isfinite(value)]
        return {'version':1,'limits':dict(self.limits),'samples':list(self.samples),
                'stopReason':self.reason,'peakProcessGroupRssBytes':max(numeric(rss),default=None),
                'minimumAvailableEstimateBytes':min(numeric(available),default=None),
                'mlxPeakMeasuredSeparately':True}
