# homeassistant_russound

## Notes


## Install Steps for test
1. Create folder structure through the file editor:
  /config/custom_components/russound_rio_3/
2. Drop the files from https://github.com/nat-neels/homeassistant_russound into the folder created in step 1.
  - Check your version of Python on your HA instance:
  - If you are using 3.11 or higher keep 'rio.py' and delete all other rio_python_x.x.py' files.
  - If you are using 3.10 delete 'rio.py' and all other rio_python_x.x.py' files EXCEPT 'rio_python_3_10.py' then rename 'rio_python_3_10.py' 'to rio.py'.
  - If you are using 3.9 or lower delete 'rio.py' and all other rio_python_x.x.py' files EXCEPT 'rio_python_3_9.py' then rename 'rio_python_3_9.py' 'to rio.py'.
3. In your config/configuration.yaml file create the media player integration for the platform. Shown in the Media Player Platform section of this readme.
4. Restart Home assistant

#### Media Player Platform
```
media_player:
#Russound Integration
  - platform: russound_rio
    host: XXX.XXX.XXX.XXX #Put your Russound Static IP Here
    name: Russound
```
