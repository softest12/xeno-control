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

### Headset (`413c:a529`) - bateria ✅

O dongle usa o protocolo **Airoha RACE** dentro de dois reports da interface `0xFF13`.
Formato conforme os projetos Headroom e HaloBattery (outros headsets Airoha); confirmado
neste headset no Linux. Implementado em `xeno_control/protocols/airoha_race.py`.

```
pedido  : output report 6, 62 bytes
          06 07 80 05 5a 03 00 d6 0c 00 00 ... (zeros)
          |  |  |  '-- quadro RACE: 05 | tipo 5a (pedido) | len u16le = 3 | opcode u16le 0x0cd6 | 00
          |  |  '-- destino: 80 = headset, 00 = o próprio dongle (que não tem bateria)
          |  '-- tamanho do quadro RACE (1 byte)
          '-- report id
resposta: input report 7 lido com GET_REPORT (HIDIOCGINPUT), guarda o último estado
          07 <tamanho> 80 05 5b 03 00 d6 0c 00 05 5d 05 00 d6 0c 00 00 3a
                         '-- confirmação (5b)   '-- indicação (5d): [status, papel, %] -> 0x3a = 58%
```

O report 7 também traz notificações periódicas (opcode `0x2cb1`, tipo `5d`). **Nunca** envie
opcodes nas faixas `0x0400-0x04FF` e `0x1C00-0x1CFF`: apagam flash / atualizam firmware.

### Mouse (`413c:2120`) - bateria ❌ (tentativas)

- O receptor do *teclado* Alienware Pro (`413c:211d`) responde à bateria com o feature
  report 0 `0e a1 00...` (projeto HaloBattery). No receptor do mouse, o mesmo pedido como
  feature report é recusado (`EPIPE`) e como output report não tem resposta.
- Parado, o mouse não envia nada nas interfaces `0xFF00`/`0xFF20`. Precisa de captura no Windows.
- Com cabo USB-C o mouse aparece como `413c:211f` ("Alienware Pro Gaming Mouse"), com as
  mesmas interfaces. Ali o feature report `0e a1` é **aceito**, mas não há resposta (nem input
  em `0xFF00`/`0xFF20`, nem GET_FEATURE). O protocolo do mouse é outro.
- Ao ligar, o receptor mandou sozinho em `0xFF00`: `c0 09 01 00 dd 00...`.
- O report de movimento tem 5 bytes do fabricante; normalmente `03 00 00 00 00`, uma vez
  `03 44 00 00 00` num clique. Significado desconhecido.
