# homeassistant_russound

## Install Steps for test
1. Create folder structure through the file editor:
  /config/custom_components/russound_rio_3/
2. Drop the files from https://github.com/nat-neels/homeassistant_russound into the folder created in step 1.
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
