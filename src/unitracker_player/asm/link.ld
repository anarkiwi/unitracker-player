/* A PSID image at __load: init first. */

MEMORY {
  ram (rwx) : ORIGIN = __load, LENGTH = 0xD000 - __load
}

SECTIONS {
  .text : { *(.init) *(.text*) *(.rodata*) } >ram
  .bss (NOLOAD) : { *(.bss*) } >ram
}

ASSERT(ADDR(.bss) + SIZEOF(.bss) <= 0xD000, "The image does not fit below $D000.")

OUTPUT_FORMAT { TRIM(ram) }
