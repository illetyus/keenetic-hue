"""Pure allowlisted kernel proposal; no live sysctl writes at import or plan."""

CONTROLS=('fs.protected_hardlinks','fs.protected_symlinks',
          'kernel.dmesg_restrict','kernel.kptr_restrict')
ASLR='kernel.randomize_va_space'


def plan_kernel(observed):
    if not isinstance(observed,dict) or set(observed)!=set(CONTROLS)|{ASLR}:
        raise ValueError('KERNEL_CONTROL_SET')
    for name,value in observed.items():
        if value is None and name in CONTROLS:continue
        maximum=2 if name in (ASLR,'kernel.kptr_restrict') else 1
        if type(value) is not int or not 0<=value<=maximum:raise ValueError('KERNEL_CONTROL_VALUE')
    if observed[ASLR]!=2:raise ValueError('KERNEL_ASLR_BASELINE')
    return {'schema':1,'aslr':2,
            'changes':{name:{'before':observed[name],'after':1} for name in CONTROLS if observed[name]==0},
            'unsupported':[name for name in CONTROLS if observed[name] is None]}
