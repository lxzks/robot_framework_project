*** Settings ***
Documentation    L2 参考答案：轮询等待代替 Sleep
Resource         ../../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
车速应稳定在目标区间
    [Documentation]    轮询等车速进入 50~70，最多 20 秒——满足即返回，不白等
    ${speed}=    Wait Until Signal In Range    EngineData    VehicleSpeed    50    70    timeout=20
    Log    车速已稳定在 ${speed} km/h ✓

档位应随车速变为 D
    [Documentation]    车速 > 1 后档位从 P(0) 变 D(3)；区间退化成单值即精确等待
    Wait Until Signal In Range    ClusterData    GearPosition    3    3    timeout=20
    Log    档位已切换到 D ✓
