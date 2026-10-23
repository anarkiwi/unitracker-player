; llvm-mos's simulator: SIM_TICKS ticks, each a line of the cycle counter before and after
; tick and the 25 registers, in hex.

        .include "song.inc"

        .section .text.sim,"ax",@progbits
        .globl sim
sim:    ldx #0xff
        txs
        jsr init_state
        lda #mos16lo(SIM_TICKS)
        sta ticks
        lda #mos16hi(SIM_TICKS)
        sta ticks+1
loop:   lda 0xfff0
        sta before
        lda 0xfff1
        sta before+1
        lda 0xfff2
        sta before+2
        jsr tick
        lda 0xfff0
        sta after
        lda 0xfff1
        sta after+1
        lda 0xfff2
        sta after+2
        ldx #2
1:      lda before,x
        jsr hex
        dex
        bpl 1b
        lda #0x20
        sta 0xfff9
        ldx #2
2:      lda after,x
        jsr hex
        dex
        bpl 2b
        ldx #0
3:      lda #0x20
        sta 0xfff9
        lda 0xd400,x
        jsr hex
        inx
        cpx #25
        bne 3b
        lda #0x0a
        sta 0xfff9
        lda ticks
        bne 4f
        dec ticks+1
4:      dec ticks
        lda ticks
        ora ticks+1
        bne loop
        lda #0
        sta 0xfff8

hex:    pha
        lsr
        lsr
        lsr
        lsr
        jsr nibble
        pla
        and #0x0f
nibble: cmp #10
        bcc 5f
        adc #6
5:      adc #0x30
        sta 0xfff9
        rts

        .section .bss.sim,"aw",@nobits
ticks:  .zero 2
before: .zero 3
after:  .zero 3
