# Xeno Control

Central de controle **livre e de código aberto** para notebooks e periféricos
Alienware no Linux: uma alternativa ao Alienware Command Center (AWCC), que só
existe para Windows.

> Projeto independente, sem relação com a Dell/Alienware. "Alienware" é marca da Dell Inc.
> Nenhum código do AWCC é usado: o notebook é controlado pelas interfaces do
> kernel Linux, e os periféricos por protocolos reimplementados do zero.

## O que funciona

| Recurso | Status | Como |
|---|---|---|
| Perfis de energia/térmicos (Silencioso, Balanceado, Desempenho, **G-Mode**...) | ✅ | `platform_profile` do driver `alienware-wmi` |
| Temperaturas e RPM das ventoinhas | ✅ | `hwmon` (`alienware_wmi`, `coretemp`, `nvme`...) |
| Boost manual das ventoinhas | ✅ kernel 6.15+ | `fanN_boost` no perfil `custom` |
| Modo de carga da bateria (Standard, Adaptive, Fast/Express, Custom, Long_Life) e limites de carga | ✅ | `power_supply` do `dell-laptop`, ou tokens do `dell-smbios` quando o `dell-laptop` não carrega (Alienware) |
| Restaurar o perfil no boot | ✅ | `xeno-control-restore.service` |
| Detectar mouse (sem fio e com cabo) e headset Alienware | ✅ | `hidraw` |
| Headset: bateria | ✅ | protocolo Airoha RACE pelo dongle |
| Headset: EQ, sidetone | 🔬 em mapeamento | veja [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) |
| Mouse: DPI, RGB, bateria, polling | 🔬 em mapeamento | idem |
| RGB do teclado | 📋 planejado | |

## Hardware testado

> ⚠️ **Até agora o Xeno Control só foi testado em um único notebook e nos periféricos abaixo.**
> Outros modelos Alienware/Dell podem funcionar (as interfaces do kernel são as mesmas), mas
> ainda não foram verificados. Se você testar em outro modelo, abra uma issue contando o resultado.

**Notebook:** Alienware 16 Aurora **AC16250** (SKU 0D35, BIOS 1.16.0), CachyOS, kernel 7.3.3

| Recurso | Resultado |
|---|---|
| Perfis de energia, incluindo G-Mode | ✅ funciona |
| Temperaturas e ventoinhas | ✅ funciona |
| Modo de carga e limites da bateria | ✅ funciona via `dell-smbios` (o `dell-laptop` não carrega neste modelo) |
| GUI e serviço de boot | ✅ funciona |

**Periféricos:**

| Dispositivo | ID USB | Resultado |
|---|---|---|
| Alienware Pro Wireless Gaming Headset (dongle) | `413c:a529` | ✅ áudio nativo · ✅ bateria · 🔬 EQ/sidetone |
| Alienware Pro Wireless Gaming Mouse (receptor sem fio) | `413c:2120` | ✅ mouse nativo · ❌ bateria/DPI/RGB ainda não |
| Alienware Pro Wireless Gaming Mouse (com cabo USB-C) | `413c:211f` | ✅ mouse nativo · ❌ bateria/DPI/RGB ainda não |

## Instalação

Requisitos: Linux com kernel **6.15 ou mais novo** recomendado, Python 3.9+.

```bash
git clone https://github.com/softest12/xeno-control.git
cd xeno-control
./install.sh
```

O script instala o app com `pipx` (com a interface gráfica PySide6), a regra udev
para acessar mouse/headset sem `sudo` e o serviço que restaura o perfil no boot.

## Uso

```bash
xeno status                       # resumo: modelo, perfil, temperaturas, bateria, periféricos
xeno profile                      # lista perfis (* = ativo)
sudo xeno profile set performance # G-Mode
xeno sensors --watch              # temperaturas/ventoinhas ao vivo
sudo xeno profile set custom && sudo xeno fan 1 200   # ventoinha 1 no boost 200/255
xeno battery                      # modo de carga e limites
sudo xeno battery limit 60 80     # carregar só até 80% (bom pra quem fica na tomada)
xeno devices                      # mouse/headset e suas interfaces HID
xeno-control                      # interface gráfica
```

A interface gráfica roda como usuário comum e pede a senha (pkexec) só na hora de mudar algo.

## Solução de problemas

**`platform_profile` não aparece / G-Mode não aparece.** Modelos muito novos
podem ainda não estar na lista do driver. Teste forçando:

```bash
sudo modprobe -r alienware_wmi
sudo modprobe alienware_wmi force_platform_profile=1 force_gmode=1
xeno profile
```

Se funcionar, deixe permanente:

```bash
echo "options alienware_wmi force_platform_profile=1 force_gmode=1" | sudo tee /etc/modprobe.d/alienware.conf
```

e abra uma issue aqui com o modelo, para avisarmos o pessoal do kernel.

**Bateria sem `charge_types`.** O `dell-laptop` só carrega em máquinas com fabricante
"Dell Inc." no DMI; em Alienware ele falha com `No such device`. Nesse caso o Xeno Control
usa a interface `/dev/wmi/dell-smbios` (módulo `dell-smbios`, carregado por padrão) e
`xeno battery` mostra "(via dell-smbios)". Ler e mudar o modo exige root (`sudo xeno battery`);
a BIOS aceita limites de 50–95% (início) e 55–100% (fim), com pelo menos 5 pontos de diferença.

## Desenvolvimento

```bash
python -m pip install -e ".[dev,gui]"
python -m pytest
```

Os testes usam uma árvore sysfs falsa (`XENO_SYSFS_ROOT`) e rodam em qualquer sistema.

Estrutura:

```
xeno_control/
  laptop.py    perfis, ventoinhas, temperaturas, bateria (sysfs)
  hid.py       hidraw: enumeração, parser de report descriptor, monitor/envio
  devices.py   tabela de periféricos conhecidos
  cli.py       comando `xeno`
  gui.py       interface Qt
tools/hid_probe.py   sonda HID multiplataforma (útil no Windows com o AWCC aberto)
data/                regra udev, serviço systemd, atalho .desktop
```

### Ajude a mapear o mouse e o headset

Quem tem o hardware e o Windows com AWCC pode capturar o tráfego USB e
documentar os comandos. O passo a passo está em
[docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md).

## Licença

MIT
