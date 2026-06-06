#include "user.h"
#include "oled.h"
#include "dht11.h"

#define CUCUMBER 0
#define TOMATO 1
#define CABBAGE 2

// HAL库中的滴答定时器变量
extern volatile uint32_t uwTick;

//滴答定时器延时模块参数
uint32_t KEY_Tick;
uint32_t OLED_Tick;
uint32_t OLED_Recover_Tick;
uint32_t MPU_Tick;
uint32_t Delay_Tick;
uint32_t MAX30102_Tick;
uint32_t DHT11_Tick;
uint32_t Flame_Tick;
uint32_t Light_Tick;
uint32_t People_Tick;
uint32_t Long_Tick;

//按键模块参数
uint8_t KEY_Last, KEY_Value, KEY_Down, KEY_Up;
uint8_t KEY_Flag;

//OLED模块参数
uint8_t OLED_ui;
char OLED_Buff[30];
char Flame='N';//火焰
char Smoke='N';//烟雾
char Gesture[10]="Normal";//姿势
char People='N';
char WIFI[10]="NO";//WIFI状态
uint8_t Temperature=0.0f;//温度
uint8_t Humidity=0.0f;//湿度
float pitch = 0.0;     // 俯仰角（前后倾斜）
float roll = 0.0;      // 横滚角（左右倾斜）
float yaw = 0.0;       // 偏航角（旋转）
uint8_t OLED_Line = 0; 
uint8_t OLED_Line1;
char crops_string[10]="Cucumber";//作物

//蜂鸣器模块参数
uint8_t Buzzer_State;
uint8_t MPU_State;
char Buzzer_Source[16] = "None";

//DHT11模块参数
uint8_t Cucumber_Tem_High=32, Cucumber_Tem_Low=12, Cucumber_Hum_High=90, Cucumber_Hum_Low=45;
uint8_t Tomato_Tem_High=30, Tomato_Tem_Low=10, Tomato_Hum_High=80, Tomato_Hum_Low=40;
uint8_t Cabbage_Tem_High=28, Cabbage_Tem_Low=10, Cabbage_Hum_High=85 ;
uint16_t Cucumber_Light_High=2600,Cucumber_Light_Low=0,Tomato_Light_High=3200,Tomato_Light_Low=0,Cabbage_Hum_Low=45,Cabbage_Light_High=2300,Cabbage_Light_Low=0;

uint8_t T_High, T_Low, H_High, H_Low;
uint16_t Light_High, Light_Low;

typedef enum {
    BUZZER_OFF = 0,
    BUZZER_TEMP_HUM_ALERT = 1, // 温湿度报警：慢速间歇
    BUZZER_LIGHT_ALERT = 2,    // 光照报警：快速间歇000000000000000000000
    BUZZER_PEOPLE_ALERT = 3,   // 有人经过：短促单声
    BUZZER_FALL_ALERT = 4,     // 摔倒：长鸣
    BUZZER_FLAME_ALERT = 5     // 火焰：急促连续
} Buzzer_Alert_Type;

Buzzer_Alert_Type Current_Buzzer_Alert = BUZZER_OFF;
uint32_t Buzzer_Last_Tick = 0;
uint8_t Buzzer_On_Flag = 0; // 当前物理输出状态
uint16_t DHT11_Data_Valid = 0;
uint16_t Light_Data_Valid = 0;
uint32_t Buzzer_Boot_Tick = 0;
uint32_t Buzzer_Alert_Start_Tick = 0;

#define BUZZER_STARTUP_MUTE_MS 5000U
#define BUZZER_ALERT_MAX_MS 3000U

static void Buzzer_Pin_AsGpioHigh(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    __HAL_RCC_GPIOB_CLK_ENABLE();
    GPIO_InitStruct.Pin = GPIO_PIN_3;
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_3, GPIO_PIN_SET);
}

static void Buzzer_Pin_AsGpio(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    __HAL_RCC_GPIOB_CLK_ENABLE();
    GPIO_InitStruct.Pin = GPIO_PIN_3;
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
}

static void Buzzer_Pin_AsPullupInput(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    __HAL_RCC_GPIOB_CLK_ENABLE();
    GPIO_InitStruct.Pin = GPIO_PIN_3;
    GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
}

void Buzzer_ForceSilentIfOff(void)
{
    if (Current_Buzzer_Alert == BUZZER_OFF)
    {
        Buzzer_Pin_AsPullupInput();
    }
}

static void Buzzer_SetTone(uint16_t freq_hz, uint8_t duty_percent)
{
    uint32_t pclk1 = HAL_RCC_GetPCLK1Freq();
    uint32_t tim_clk;
    uint32_t tick_clk;
    uint32_t arr;
    uint32_t ccr;

    if (freq_hz < 50U) freq_hz = 50U;
    if (freq_hz > 8000U) freq_hz = 8000U;
    if (duty_percent > 90U) duty_percent = 90U;

    tim_clk = pclk1;
    if ((RCC->CFGR & RCC_CFGR_PPRE1) != RCC_HCLK_DIV1)
    {
        tim_clk = pclk1 * 2U;
    }

    tick_clk = tim_clk / (htim2.Init.Prescaler + 1U);
    arr = (tick_clk / freq_hz);
    if (arr == 0U) arr = 1U;
    arr -= 1U;
    ccr = ((arr + 1U) * duty_percent) / 100U;

    __HAL_TIM_SET_AUTORELOAD(&htim2, arr);
    __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_2, ccr);
    __HAL_TIM_SET_COUNTER(&htim2, 0U);
}

static void Buzzer_Output(uint8_t on, uint16_t freq_hz)
{
    (void)freq_hz;
    Buzzer_Pin_AsGpio();
    if (on)
    {
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_3, GPIO_PIN_RESET);
        Buzzer_On_Flag = 1U;
    }
    else
    {
        Buzzer_Pin_AsPullupInput();
        Buzzer_On_Flag = 0U;
    }
}

static void Clear_Buzzer_Alert(Buzzer_Alert_Type alert)
{
    if (Current_Buzzer_Alert == alert)
    {
        Current_Buzzer_Alert = BUZZER_OFF;
        strcpy(Buzzer_Source, "None");
    }
}

static const char *Buzzer_AlertToString(Buzzer_Alert_Type alert)
{
    switch (alert)
    {
        case BUZZER_TEMP_HUM_ALERT: return "TempHum";
        case BUZZER_LIGHT_ALERT: return "Light";
        case BUZZER_PEOPLE_ALERT: return "People";
        case BUZZER_FALL_ALERT: return "Fall";
        case BUZZER_FLAME_ALERT: return "Flame";
        default: return "None";
    }
}

//光照强度读取模块参数
uint16_t ADC_Value;
uint16_t Light;


//按键扫描函数
void KEY_Read(void)
{
    if (HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_1) == GPIO_PIN_RESET)KEY_Value = 1;
    else if (HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_0) == GPIO_PIN_RESET)KEY_Value = 2;
    else KEY_Value = 0;
    // 计算按键下降沿和上升沿
    KEY_Down = KEY_Value & (KEY_Value ^ KEY_Last);
    KEY_Up = (~KEY_Value) & (KEY_Value ^ KEY_Last);

    // 更新上次按键状态
    KEY_Last = KEY_Value;
}

void KEY_Process(void)
{
    if (uwTick - KEY_Tick < 20) return;
    KEY_Tick = uwTick;
    KEY_Read();
    // 这是你原来的逻辑，我只修BUG，不改操作！
if (KEY_Up == 1)
{
    // 作物选择
    if(OLED_ui==0)
    {
        OLED_Line++;
        if(OLED_Line>2) OLED_Line=0;
    }
}

// 修复：按下DOWN时记录时间
/*if(KEY_Down == 1)
{
    Long_Tick = uwTick;
}

// 修复：长按确定键 进入/退出 修改模式（BUG核心修复）
if(KEY_Value == 1)
{
    if((uwTick - Long_Tick) > 2000U)
    {
        KEY_Flag = !KEY_Flag;   // 翻转 0/1
        Long_Tick = uwTick;     // 防止连续触发
    }
}
else
{
    // 松开复位，必须加！
    Long_Tick = uwTick;
}

// 修复：查看模式下，上键切换选项（0~5）
if(KEY_Up == 1 && KEY_Flag == 0)
{
    OLED_Line1++;
    if(OLED_Line1 > 5) OLED_Line1 = 0;
}

// 修复：修改模式下，上键 ++ 数值
if(KEY_Up == 1 && KEY_Flag == 1)
{
    if(OLED_Line == CUCUMBER)
    {
        if(OLED_Line1==0) Cucumber_Tem_High++;
        else if(OLED_Line1==1) Cucumber_Tem_Low++;
        else if(OLED_Line1==2) Cucumber_Hum_High++;
        else if(OLED_Line1==3) Cucumber_Hum_Low++;
        else if(OLED_Line1==4) Cucumber_Light_High++;
        else if(OLED_Line1==5) Cucumber_Light_Low++;

        // 修复：超过100 归100，不是归0
        if(Cucumber_Tem_High>100) Cucumber_Tem_High=100;
        if(Cucumber_Tem_Low>100) Cucumber_Tem_Low=100;
        if(Cucumber_Hum_High>100) Cucumber_Hum_High=100;
        if(Cucumber_Hum_Low>100) Cucumber_Hum_Low=100;
        if(Cucumber_Light_High>100) Cucumber_Light_High=100;
        if(Cucumber_Light_Low>100) Cucumber_Light_Low=100;

        // 上限必须 >= 下限
        if(Cucumber_Tem_High < Cucumber_Tem_Low) Cucumber_Tem_High = Cucumber_Tem_Low;
        if(Cucumber_Hum_High < Cucumber_Hum_Low) Cucumber_Hum_High = Cucumber_Hum_Low;
        if(Cucumber_Light_High < Cucumber_Light_Low) Cucumber_Light_High = Cucumber_Light_Low;
    }

    if(OLED_Line == TOMATO)
    {
        if(OLED_Line1==0) Tomato_Tem_High++;
        else if(OLED_Line1==1) Tomato_Tem_Low++;
        else if(OLED_Line1==2) Tomato_Hum_High++;
        else if(OLED_Line1==3) Tomato_Hum_Low++;
        else if(OLED_Line1==4) Tomato_Light_High++;
        else if(OLED_Line1==5) Tomato_Light_Low++;

        if(Tomato_Tem_High>100) Tomato_Tem_High=100;
        if(Tomato_Tem_Low>100) Tomato_Tem_Low=100;
        if(Tomato_Hum_High>100) Tomato_Hum_High=100;
        if(Tomato_Hum_Low>100) Tomato_Hum_Low=100;
        if(Tomato_Light_High>100) Tomato_Light_High=100;
        if(Tomato_Light_Low>100) Tomato_Light_Low=100;

        if(Tomato_Tem_High < Tomato_Tem_Low) Tomato_Tem_High = Tomato_Tem_Low;
        if(Tomato_Hum_High < Tomato_Hum_Low) Tomato_Hum_High = Tomato_Hum_Low;
        if(Tomato_Light_High < Tomato_Light_Low) Tomato_Light_High = Tomato_Light_Low;
    }

    if(OLED_Line == CABBAGE)
    {
        if(OLED_Line1==0) Cabbage_Tem_High++;
        else if(OLED_Line1==1) Cabbage_Tem_Low++;
        else if(OLED_Line1==2) Cabbage_Hum_High++;
        else if(OLED_Line1==3) Cabbage_Hum_Low++;
        else if(OLED_Line1==4) Cabbage_Light_High++;
        else if(OLED_Line1==5) Cabbage_Light_Low++;

        if(Cabbage_Tem_High>100) Cabbage_Tem_High=100;
        if(Cabbage_Tem_Low>100) Cabbage_Tem_Low=100;
        if(Cabbage_Hum_High>100) Cabbage_Hum_High=100;
        if(Cabbage_Hum_Low>100) Cabbage_Hum_Low=100;
        if(Cabbage_Light_High>100) Cabbage_Light_High=100;
        if(Cabbage_Light_Low>100) Cabbage_Light_Low=100;

        if(Cabbage_Tem_High < Cabbage_Tem_Low) Cabbage_Tem_High = Cabbage_Tem_Low;
        if(Cabbage_Hum_High < Cabbage_Hum_Low) Cabbage_Hum_High = Cabbage_Hum_Low;
        if(Cabbage_Light_High < Cabbage_Light_Low) Cabbage_Light_High = Cabbage_Light_Low;
    }
}*/
    else if(KEY_Down == 2)
    {
        OLED_ui++;
        if(OLED_ui>3) OLED_ui=0;
    }
}

void Buzzer_Proc(void)
{
    uint32_t current_tick = uwTick;

    if (Buzzer_Boot_Tick == 0U)
    {
        Buzzer_Boot_Tick = current_tick;
    }
    if ((current_tick - Buzzer_Boot_Tick) < BUZZER_STARTUP_MUTE_MS)
    {
        Buzzer_Output(0U, 1000U);
        return;
    }

    if (Current_Buzzer_Alert != BUZZER_OFF && Buzzer_Alert_Start_Tick != 0U)
    {
        if ((current_tick - Buzzer_Alert_Start_Tick) >= BUZZER_ALERT_MAX_MS)
        {
            Current_Buzzer_Alert = BUZZER_OFF;
            strcpy(Buzzer_Source, "None");
            Buzzer_Output(0U, 1000U);
            return;
        }
    }

    uint32_t delta = current_tick - Buzzer_Last_Tick;
    uint16_t tone_hz = 2000U;
    uint8_t buzzer_on = 0U;

    switch (Current_Buzzer_Alert)
    {
        case BUZZER_OFF:
            buzzer_on = 0U;
            break;

        case BUZZER_TEMP_HUM_ALERT:
            /* 温湿度超限: 低频慢速双响 */
            tone_hz = 1200U;
            if (delta < 120U)
            {
                buzzer_on = 1U;
            }
            else if (delta < 260U)
            {
                buzzer_on = 0U;
            }
            else if (delta < 380U)
            {
                buzzer_on = 1U;
            }
            else
            {
                buzzer_on = 0U;
            }
            if (delta >= 1600U)
            {
                Buzzer_Last_Tick = current_tick;
            }
            break;

        case BUZZER_LIGHT_ALERT:
            /* 光照过高: 中高频快速闪鸣 */
            tone_hz = 2100U;
            if (delta >= 160U)
            {
                Buzzer_Last_Tick = current_tick;
                delta = 0U;
            }
            buzzer_on = (delta < 80U) ? 1U : 0U;
            break;

        case BUZZER_PEOPLE_ALERT:
            /* 人体经过: 单次短促高音 */
            tone_hz = 2600U;
            if (delta < 120U)
            {
                buzzer_on = 1U;
            }
            else
            {
                buzzer_on = 0U;
                Current_Buzzer_Alert = BUZZER_OFF;
            }
            break;

        case BUZZER_FALL_ALERT:
            /* 摔倒: 低高音交替的警笛声 */
            if (delta >= 500U)
            {
                Buzzer_Last_Tick = current_tick;
                delta = 0U;
            }
            tone_hz = (delta < 250U) ? 700U : 1400U;
            buzzer_on = 1U;
            break;

        case BUZZER_FLAME_ALERT:
            /* 火焰: 急促高频脉冲 */
            if (delta >= 180U)
            {
                Buzzer_Last_Tick = current_tick;
                delta = 0U;
            }
            tone_hz = (delta < 90U) ? 3300U : 2800U;
            buzzer_on = (delta < 130U) ? 1U : 0U;
            break;

        default:
            buzzer_on = 0U;
            break;
    }

    Buzzer_Output(buzzer_on, tone_hz);
}

void Set_Buzzer_Alert(Buzzer_Alert_Type alert)
{
    // 优先级: 摔倒 > 火焰 > 温湿度 > 光照 > 有人 > 无
    // 如果当前报警优先级高于新报警，则忽略新报警（除非是关闭）
    if (alert == BUZZER_OFF) {
        Current_Buzzer_Alert = BUZZER_OFF;
        strcpy(Buzzer_Source, "None");
        Buzzer_Alert_Start_Tick = 0U;
        Buzzer_Output(0U, 1000U);
        return;
    }

    if (Current_Buzzer_Alert == BUZZER_FALL_ALERT) return; // 摔倒最高优先级，不被覆盖
    if (Current_Buzzer_Alert == BUZZER_FLAME_ALERT && alert != BUZZER_FALL_ALERT) return; // 火焰次之
    
    
    
    int current_priority = 0;
    int new_priority = 0;
    
    switch(Current_Buzzer_Alert) {
        case BUZZER_FALL_ALERT: current_priority = 5; break;
        case BUZZER_FLAME_ALERT: current_priority = 4; break;
        case BUZZER_TEMP_HUM_ALERT: current_priority = 3; break;
        case BUZZER_LIGHT_ALERT: current_priority = 3; break;
        case BUZZER_PEOPLE_ALERT: current_priority = 1; break;
        default: current_priority = 0; break;
    }

    switch(alert) {
        case BUZZER_FALL_ALERT: new_priority = 5; break;
        case BUZZER_FLAME_ALERT: new_priority = 4; break;
        case BUZZER_TEMP_HUM_ALERT: new_priority = 3; break;
        case BUZZER_LIGHT_ALERT: new_priority = 3; break;
        case BUZZER_PEOPLE_ALERT: new_priority = 1; break;
        default: new_priority = 0; break;
    }

    if (new_priority >= current_priority) {
        if (Current_Buzzer_Alert != alert)
        {
            Buzzer_Alert_Start_Tick = uwTick;
        }
        Current_Buzzer_Alert = alert;
        strcpy(Buzzer_Source, Buzzer_AlertToString(alert));
        Buzzer_Last_Tick = uwTick; // 重置时间基准
        Buzzer_Output(0U, 1000U); // 强制复位物理输出，交给Proc重新拉起
    }
}

void Corn_sec(void)
{
    if(OLED_Line == CUCUMBER)
    {
        strcpy(crops_string, "Cucumber");
        T_High=Cucumber_Tem_High;
        T_Low=Cucumber_Tem_Low;
        H_High=Cucumber_Hum_High;
        H_Low=Cucumber_Hum_Low;
        Light_High=Cucumber_Light_High;
        Light_Low=Cucumber_Light_Low;
    }
    else if(OLED_Line == TOMATO)
    {
        strcpy(crops_string, "Tomato");
        T_High=Tomato_Tem_High;
        T_Low=Tomato_Tem_Low;
        H_High=Tomato_Hum_High;
        H_Low=Tomato_Hum_Low;
        Light_High=Tomato_Light_High;
        Light_Low=Tomato_Light_Low;
    }
    else if(OLED_Line == CABBAGE)
    {
        strcpy(crops_string, "Cabbage");
        T_High=Cabbage_Tem_High;
        T_Low=Cabbage_Tem_Low;
        H_High=Cabbage_Hum_High;
        H_Low=Cabbage_Hum_Low;
        Light_High=Cabbage_Light_High;
        Light_Low=Cabbage_Light_Low;
    }
}

// 显示123的函数
void OLED_Display(void)
{
    if (uwTick - OLED_Tick < 20) return;
    OLED_Tick = uwTick;
    if(OLED_ui==0)
    {
        OLED_NewFrame();
        sprintf(OLED_Buff, "Flame:%c", Flame);
        OLED_PrintASCIIString(0,0,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, "People:%c", People);
        OLED_PrintASCIIString(60,0,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, "crops:%s", crops_string);
        OLED_PrintASCIIString(0,15,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, "Gesture:%s", Gesture);
        OLED_PrintASCIIString(0,30,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, "WIFI:%s", WIFI);
        OLED_PrintASCIIString(0,45,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        OLED_ShowFrame();
    }
    else if(OLED_ui==1)
    {
        OLED_NewFrame();
        sprintf(OLED_Buff, "Temp:%02dC", Temperature);
        OLED_PrintASCIIString(0,0,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, "Humi:%02d%%", Humidity);
        OLED_PrintASCIIString(0,15,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, "Light:%d", Light);
        OLED_PrintASCIIString(0,30,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, "Alarm:%s", Buzzer_Source);
        OLED_PrintASCIIString(0,45,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        OLED_ShowFrame();
    }
    else if(OLED_ui==2)
    {
        OLED_NewFrame();
        sprintf(OLED_Buff, "Pitch:%6.1f", pitch);
        OLED_PrintASCIIString(0,0,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, "Roll :%6.1f", roll);
        OLED_PrintASCIIString(0,15,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, "Yaw  :%6.1f", yaw);
        OLED_PrintASCIIString(0,30,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        OLED_ShowFrame();
    }
    else if(OLED_ui==3)
    {
        OLED_NewFrame();
        sprintf(OLED_Buff, " T_H:%d", T_High);
        OLED_PrintASCIIString(0,0,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, " T_L:%d", T_Low);
        OLED_PrintASCIIString(55,0,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, " H_H:%d", H_High);
        OLED_PrintASCIIString(0,15,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, " H_L:%d", H_Low);
        OLED_PrintASCIIString(55,15,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
         sprintf(OLED_Buff, " L_H:%d", Light_High);
        OLED_PrintASCIIString(0,30,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        sprintf(OLED_Buff, " L_L:%d", Light_Low);
        OLED_PrintASCIIString(65,30,OLED_Buff,&afont16x8,OLED_COLOR_NORMAL);
        OLED_ShowFrame();
    }
}

//MPU6050数据处理函数
void MPU6050_Process(void)
{
    // DMP读取不宜过快（DMP一般配置100Hz或200Hz输出），增加读取间隔防空等
    if(uwTick - MPU_Tick < 50) return;
    MPU_Tick = uwTick;

    // 修复：只有成功获取到DMP有效数据，才进行姿态判断，否则pitch和roll会被清零或刷成垃圾值导致假平稳
    if (mpu_dmp_get_data(&pitch, &roll, &yaw) == 0)
    {
        if(pitch > 60.0f || pitch < -60.0f || roll > 60.0f || roll < -60.0f) 
        {
            strcpy(Gesture, "Fall");
            Set_Buzzer_Alert(BUZZER_FALL_ALERT);
        } 
        else 
        {
            strcpy(Gesture, "Normal");
            Clear_Buzzer_Alert(BUZZER_FALL_ALERT);
        }
    }
}

// DHT11数据处理函数中增加温湿度报警逻辑
void DHT11_Process(void)
{
    if(uwTick - DHT11_Tick < 1000) return;
    DHT11_Tick = uwTick;
    DHT11_ReadData(&Temperature, &Humidity);

    if (Humidity == 0U || Humidity > 100U || Temperature > 80U)
    {
        DHT11_Data_Valid = 0U;
        Clear_Buzzer_Alert(BUZZER_TEMP_HUM_ALERT);
        return;
    }
    DHT11_Data_Valid = 1U;
    
    // 根据当前作物判断温湿度是否超限
    uint8_t t_high, t_low, h_high, h_low;
    if(OLED_Line == CUCUMBER) {
        t_high = Cucumber_Tem_High; t_low = Cucumber_Tem_Low;
        h_high = Cucumber_Hum_High; h_low = Cucumber_Hum_Low;
    } else if(OLED_Line == TOMATO) {
        t_high = Tomato_Tem_High; t_low = Tomato_Tem_Low;
        h_high = Tomato_Hum_High; h_low = Tomato_Hum_Low;
    } else if(OLED_Line == CABBAGE) {
        t_high = Cabbage_Tem_High; t_low = Cabbage_Tem_Low;
        h_high = Cabbage_Hum_High; h_low = Cabbage_Hum_Low;
    } else {
        return; // 默认情况
    }

    uint8_t t_min = (t_high < t_low) ? t_high : t_low;
    uint8_t t_max = (t_high > t_low) ? t_high : t_low;
    uint8_t h_min = (h_high < h_low) ? h_high : h_low;
    uint8_t h_max = (h_high > h_low) ? h_high : h_low;

    if (DHT11_Data_Valid && (Temperature > t_max || Temperature < t_min || Humidity > h_max || Humidity < h_min)) {
        Set_Buzzer_Alert(BUZZER_TEMP_HUM_ALERT);
    } else {
        Clear_Buzzer_Alert(BUZZER_TEMP_HUM_ALERT);
    }
}


//光照强度读取模块参数
void Light_Process(void)
{
    static uint8_t light_warmup_done = 0U;
    static uint8_t light_alert_latched = 0U;

    // 先更新当前作物阈值，避免主循环调用顺序导致使用旧阈值
    Corn_sec();

    if (uwTick - Light_Tick < 1000) return;
    Light_Tick = uwTick;
    // 同步读取ADC，避免DMA异步导致比较时拿到旧值
    HAL_ADC_Start(&hadc1);
    if (HAL_ADC_PollForConversion(&hadc1, 10U) == HAL_OK)
    {
        ADC_Value = (uint16_t)HAL_ADC_GetValue(&hadc1);
    }
    HAL_ADC_Stop(&hadc1);

    // 保持传感器物理方向: 光越强 -> ADC越小 -> Light越大
    // 这样可以继续使用 "Light > Light_High" 作为过强光照报警条件
    Light = 4096- ADC_Value; // 12位ADC，范围0-4095

    if (!light_warmup_done)
    {
        light_warmup_done = 1U;
        Light_Data_Valid = 0U;
        Clear_Buzzer_Alert(BUZZER_LIGHT_ALERT);
        return;
    }
    Light_Data_Valid = 1U;
    
    // 使用当前作物阈值判断是否越界: 仅当高于上限时报警
    uint16_t light_high_limit;
    uint16_t light_clear_high;

    /* 兼容高低阈值被填反 (取上限较大的那个为准) */
    light_high_limit = (Light_High > Light_Low) ? Light_High : Light_Low;

    /* 回差: 报警后低于 (阈值 - 30) 再清除，避免临界值频繁触发抖动。因为Light范围接近4095，所以回差设大一点 */
    light_clear_high = (light_high_limit > 30U) ? (light_high_limit - 30U) : light_high_limit;

    if (Light_Data_Valid && (Light > light_high_limit))
    {
        light_alert_latched = 1U;
        Set_Buzzer_Alert(BUZZER_LIGHT_ALERT);
    }
    else
    {
        if (light_alert_latched && (Light > light_clear_high))
        {
            Set_Buzzer_Alert(BUZZER_LIGHT_ALERT);
        }
        else
        {
            light_alert_latched = 0U;
            Clear_Buzzer_Alert(BUZZER_LIGHT_ALERT);
        }
    }
}


void Flame_Process(void)
{
    static uint8_t flame_low_cnt = 0U;
    uint8_t flame_raw_low;

    if(uwTick - Flame_Tick < 100) return;
    Flame_Tick = uwTick;

    flame_raw_low = (HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_5) == GPIO_PIN_RESET) ? 1U : 0U;
    if (flame_raw_low)
    {
        if (flame_low_cnt < 3U) flame_low_cnt++;
    }
    else
    {
        flame_low_cnt = 0U;
    }

    if(flame_low_cnt >= 3U)
    {
        Flame = 'Y';
        Set_Buzzer_Alert(BUZZER_FLAME_ALERT);
    }
    else
    {
        Flame = 'N';
        Clear_Buzzer_Alert(BUZZER_FLAME_ALERT);
    }
}
void People_Process(void)
{
    static uint8_t people_low_cnt = 0U;
    static uint8_t people_state_low = 0U;
    uint8_t people_raw_low;

    if(uwTick - People_Tick < 100) return;
    People_Tick = uwTick;

    people_raw_low = (HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_4) == GPIO_PIN_RESET) ? 1U : 0U;
    if (people_raw_low)
    {
        if (people_low_cnt < 3U) people_low_cnt++;
    }
    else
    {
        people_low_cnt = 0U;
    }

    if (people_low_cnt >= 3U)
    {
        if (!people_state_low)
        {
            people_state_low = 1U;
            Set_Buzzer_Alert(BUZZER_PEOPLE_ALERT);
        }
        People = 'Y';
    }
    else
    {
        people_state_low = 0U;
        People = 'N';
    }
}