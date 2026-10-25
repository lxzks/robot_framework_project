*** Settings ***
Documentation    L2 练习：轮询等待（把 TODO 补完，见 README.md）
Resource         ../../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
车速应稳定在目标区间
    [Documentation]    用轮询等待车速进入 50~70（禁用 Sleep！）
    # TODO 1: 用 Wait Until Signal In Range 等车速进入 50~70，timeout=20
    ...

档位应随车速变为 D
    [Documentation]    等档位信号变成 3（D 档）
    # TODO 2: 提示 —— 区间写 3 3 就是"等它精确等于 3"
    ...
