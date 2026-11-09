# Mapeando o protocolo do mouse e do headset

A Dell não publica os comandos USB dos periféricos Alienware. O Xeno Control
**não copia código do Alienware Command Center**: o protocolo é descoberto
observando o tráfego USB (engenharia reversa para interoperabilidade) e
reimplementado do zero.

## Dispositivos alvo

| Dispositivo | VID:PID | Interface vendor | Usage page |
|---|---|---|---|
| Alienware Pro Wireless Gaming Headset (`AWPRO H Wireless`) | `413c:a529` | 5 (Col02) | `0xFF13` |
| Alienware Pro Wireless Gaming Mouse (`Alienware Pro M Gaming Receiver`) | `413c:2120` | 2 | `0xFF00` |
| | | 3 | `0xFF20` |

As outras interfaces (áudio USB, mouse, teclado, consumer control) já
funcionam nativamente no Linux.

## 1. Capturar no Windows (com o AWCC instalado)

1. Instale o [Wireshark](https://www.wireshark.org/) marcando **USBPcap** na instalação. Reinicie.
2. Abra o Wireshark como administrador e escolha a interface `USBPcapN` onde
   o dongle está conectado.
3. Filtro de exibição, para ver só os periféricos:

   ```
   usb.idVendor == 0x413c || usb.device_address == <endereço do dongle>
   ```

   Depois de achar o endereço, use `usb.device_address == N && usb.transfer_type == 0x01 || usb.setup.bRequest == 0x09`
   (`0x09` = HID SET_REPORT, que é como o AWCC manda comandos).
4. Faça **uma ação por vez** no AWCC e anote o horário:
   - mudar DPI para um valor; depois outro valor
   - mudar a cor/efeito do RGB
   - mudar a taxa de polling
   - no headset: mudar o EQ, o sidetone, o tempo de desligamento
   - deixar parado ~1 min (o AWCC consulta a bateria periodicamente)
5. Salve como `.pcapng` em `captures/` (está no `.gitignore`; não suba capturas com dados pessoais).

Para ver as **respostas** sem Wireshark, com o AWCC aberto:

```
pip install hidapi
python tools/hid_probe.py --listen 413c:a529 --seconds 60
python tools/hid_probe.py --listen 413c:2120 --seconds 60
```

## 2. Reproduzir no Linux

```
xeno devices                        # mostra /dev/hidrawN, report IDs e tamanhos
xeno hid monitor 413c:a529 --vendor-only
xeno hid send /dev/hidraw5 "<bytes capturados>"            # output report
xeno hid send /dev/hidraw5 "<bytes>" --feature              # feature report
xeno hid get-feature /dev/hidraw5 0x<report-id> --length 64
```

O primeiro byte enviado é sempre o **report ID** (use `00` se o descritor não tiver IDs).

## 3. Documentar e implementar

Para cada comando descoberto, registre aqui:

```
### <dispositivo> - <função>
tipo: SET_REPORT feature/output, report id 0x..
request : 02 0a 01 ...
resposta: 02 0a 01 5f ...   (byte 3 = bateria %)
```

Depois crie `xeno_control/protocols/<dispositivo>.py` e aponte `protocol=` em
`xeno_control/devices.py`.

## Segurança

- Teste com valores que você observou na captura. Comandos aleatórios podem
  entrar em modo de atualização de firmware.
- Nunca envie nada que pareça update de firmware (rajadas grandes de dados logo após um comando "enter bootloader").

## Comandos mapeados

_Nenhum ainda. Contribuições são bem-vindas!_
