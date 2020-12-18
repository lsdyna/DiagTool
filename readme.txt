说明：
1、zlgcan.py为zlgcan python版本接口，目前支持CAN/CANFD接口操作。
2、demo/zlgcan_demo.py为基于tkinter实现的GUI例程,其实现了USBCAN(FD)系列产品的数据收发功能。
   其中，demo/output/zlgcan_demo.exe为zlgcan_demo.py用pyinstaller打包后生成的exe文件。
3、当前Demo支持Python 3.6版本,zlgcan.py支持Python2.7及3.6版本。
4、例程里面所使用的函数库不能保证一定是最新的，如果调试或者做二次开发，请一定要从官网的二次开发接口函数库里面拷贝出来替代，二次开发接口函数库是保持最新的。
   函数库链接：https://www.zlg.cn/data/upload/software/Can/CAN_lib.rar

使用说明：
1、直接运行zlgcan.py或zlgcan_demo.py时，需先将lib\kerneldlls目录拷贝到python安装根目录下，并将zlgcan.dll拷贝到当前目录下。
   原因：由于Python运行时，zlgcan.dll中的GetModuleFileName获取的当前目录指向了Python安装目录！






