package agora

import (
	"reflect"
	"testing"
)

type recordingParameterSetter struct {
	values []string
}

func (s *recordingParameterSetter) SetParameters(value string) int {
	s.values = append(s.values, value)
	return 0
}

func TestConfigureRTCConnectionParametersIncludesLANAcceleration(t *testing.T) {
	setter := &recordingParameterSetter{}
	if err := configureRTCConnectionParameters(setter); err != nil {
		t.Fatal(err)
	}

	want := []string{
		`{"che.audio.custom_payload_type":69}`,
		`{"rtc.lan_accelerate_enable": true}`,
	}
	if !reflect.DeepEqual(setter.values, want) {
		t.Fatalf("parameters = %#v, want %#v", setter.values, want)
	}
}
