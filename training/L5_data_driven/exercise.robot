*** Settings ***
Documentation    L5 练习：数据驱动（把 TODO 补完，见 README.md）
Resource         ../../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
读取多个 DID 应正确
    [Documentation]    用 Template 覆盖 F187/F189/F18A 三组数据
    # TODO 1: 加 [Template] 设置，指向你在下面定义的关键字
    #         然后填三行数据：DID 与期望值（见 README 表格）
    [Template]    ...
    ...

多个信号应在各自范围内
    [Documentation]    用 Template 覆盖 3 个信号的范围断言
    # TODO 2: 同理，参数顺序：报文 信号 低值 高值
    ...

*** Keywords ***
# TODO 3: 定义被 Template 调用的两个关键字（带 [Arguments]）
#   读取 DID 并断言    ${did}    ${expected}
#   信号应落在范围内    ${message}    ${signal}    ${low}    ${high}
